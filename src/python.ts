import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const workerPath = fileURLToPath(new URL('../python/reasoning_memory.py', import.meta.url))

export async function runReasoningCore<T>(
  executable: string,
  input: unknown,
  signal: AbortSignal,
): Promise<T> {
  if (signal.aborted) throw signal.reason ?? new Error('operation aborted')

  return new Promise<T>((resolve, reject) => {
    const child = spawn(executable, [workerPath], {
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    })
    let stdout = ''
    let stderr = ''
    let settled = false

    const finish = (error?: Error, value?: T) => {
      if (settled) return
      settled = true
      signal.removeEventListener('abort', abort)
      if (error) reject(error)
      else resolve(value as T)
    }

    const abort = () => {
      child.kill()
      finish(signal.reason instanceof Error ? signal.reason : new Error('operation aborted'))
    }

    signal.addEventListener('abort', abort, { once: true })
    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')
    child.stdout.on('data', chunk => { stdout += chunk })
    child.stderr.on('data', chunk => { stderr += chunk })
    child.stdin.on('error', error => finish(error))
    child.once('error', error => finish(error))
    child.once('close', code => {
      if (settled) return
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
    child.stdin.end(JSON.stringify(input))
  })
}
