import { spawn } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const workerPath = fileURLToPath(new URL('../python/reasoning_memory.py', import.meta.url))

// JSON stdin/stdout bridge into the Python core, with a file-transport
// fallback: some host environments (sandboxed runtimes, restricted Windows
// service contexts) refuse pipe-based child stdio with EPERM. When the pipe
// spawn fails that way, the same worker runs in `--file-io` mode — request
// written to a temp file, response read back from another — so the plugin
// keeps working without pipe access.
export async function runReasoningCore<T>(
  executable: string,
  input: unknown,
  signal: AbortSignal,
): Promise<T> {
  if (signal.aborted) throw signal.reason ?? new Error('operation aborted')

  return new Promise<T>((resolve, reject) => {
    let settled = false
    let child: ReturnType<typeof spawn> | undefined

    const finish = (error?: Error, value?: T) => {
      if (settled) return
      settled = true
      signal.removeEventListener('abort', abort)
      if (error) reject(error)
      else resolve(value as T)
    }

    const abort = () => {
      child?.kill()
      finish(signal.reason instanceof Error ? signal.reason : new Error('operation aborted'))
    }

    signal.addEventListener('abort', abort, { once: true })

    const startFileTransport = () => {
      const inputFile = join(tmpdir(), `rm-req-${randomUUID()}.json`)
      const outputFile = join(tmpdir(), `rm-res-${randomUUID()}.json`)
      const errorFile = join(tmpdir(), `rm-err-${randomUUID()}.txt`)
      try {
        writeFileSync(inputFile, JSON.stringify(input))
      } catch (error) {
        finish(error instanceof Error ? error : new Error(String(error)))
        return
      }
      child = spawn(executable, [workerPath, '--file-io', inputFile, outputFile, errorFile], {
        stdio: 'ignore',
        windowsHide: true,
      })
      child.once('error', error => finish(error))
      child.once('close', code => {
        if (settled) return
        try {
          if (code !== 0) {
            let detail = ''
            try {
              detail = readFileSync(errorFile, 'utf8').trim()
            } catch {
              // The worker may have died before writing diagnostics.
            }
            finish(new Error(detail || `reasoning-memory Python core exited ${code} in file-io mode`))
            return
          }
          finish(undefined, JSON.parse(readFileSync(outputFile, 'utf8')) as T)
        } catch (error) {
          finish(new Error(`reasoning-memory Python core returned invalid JSON: ${String(error)}`))
        } finally {
          for (const file of [inputFile, outputFile, errorFile]) {
            try {
              rmSync(file, { force: true })
            } catch {
              // Temp-file cleanup is best effort.
            }
          }
        }
      })
    }

    const startPipeTransport = () => {
      const pipeChild = spawn(executable, [workerPath], {
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true,
      })
      child = pipeChild
      let stdout = ''
      let stderr = ''
      let pipeErrored = false
      pipeChild.stdout.setEncoding('utf8')
      pipeChild.stderr.setEncoding('utf8')
      pipeChild.stdout.on('data', chunk => { stdout += chunk })
      pipeChild.stderr.on('data', chunk => { stderr += chunk })
      pipeChild.stdin.on('error', error => {
        // A failed pipe spawn surfaces on stdin AND on the child. The child's
        // 'error' handler owns the EPERM fallback, so a stdin EPERM must not
        // settle the promise first and skip the file transport.
        if ((error as NodeJS.ErrnoException).code === 'EPERM') return
        finish(error)
      })
      pipeChild.once('error', error => {
        // A failed pipe spawn still emits 'close' afterwards; mark it so the
        // close handler cannot settle while the file transport is running.
        pipeErrored = true
        if ((error as NodeJS.ErrnoException).code === 'EPERM') {
          startFileTransport()
        } else {
          finish(error)
        }
      })
      pipeChild.once('close', code => {
        if (settled || pipeErrored) return
        if (code !== 0) {
          finish(new Error(`reasoning-memory Python core exited ${code}: ${stderr.trim()}`))
          return
        }
        try {
          finish(undefined, JSON.parse(stdout) as T)
        } catch (error) {
          finish(new Error(`reasoning-memory Python core returned invalid JSON: ${String(error)}`))
        }
      })
      pipeChild.stdin.end(JSON.stringify(input))
    }

    try {
      startPipeTransport()
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'EPERM') {
        startFileTransport()
      } else {
        finish(error instanceof Error ? error : new Error(String(error)))
      }
    }
  })
}
