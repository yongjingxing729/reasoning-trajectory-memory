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
// Version gate via an exit-code probe (no stdout capture, so it also works in
// sandboxes that deny pipe-based child stdio): the probe exits 0 for a
// supported interpreter and 3 for an older one.
const versionProbe = spawnSync(
  interpreter,
  ['-c', 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 3)'],
  { stdio: 'ignore' },
)
if (versionProbe.status === 3) {
  console.error(`Python 3.10+ is required; the interpreter '${interpreter}' reports an older version.`)
  process.exit(1)
}
if (versionProbe.status !== 0) {
  console.error(`cannot verify the Python version of '${interpreter}' (probe exited ${versionProbe.status}); Python 3.10+ is required.`)
  process.exit(1)
}
const run = spawnSync(interpreter, ['-m', 'unittest', 'discover', '-s', 'test', '-v'], { stdio: 'inherit' })
process.exit(run.status ?? 1)
