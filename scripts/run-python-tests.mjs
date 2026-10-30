// Cross-platform Python test launcher: GitHub Actions ubuntu runners only
// ship `python3`, Windows runners only `python`. Try candidates in order and
// run the unittest suite with the first working interpreter.
import { spawnSync } from 'node:child_process'

const candidates = process.platform === 'win32' ? ['python', 'py', 'python3'] : ['python3', 'python']
let interpreter = null
for (const candidate of candidates) {
  const probe = spawnSync(candidate, ['--version'], { stdio: 'ignore' })
  if (probe.status === 0) {
    interpreter = candidate
    break
  }
}
if (!interpreter) {
  console.error(`no Python interpreter found (tried: ${candidates.join(', ')}); Python 3.10+ is required`)
  process.exit(1)
}
const run = spawnSync(interpreter, ['-m', 'unittest', 'discover', '-s', 'test', '-v'], { stdio: 'inherit' })
process.exit(run.status ?? 1)
