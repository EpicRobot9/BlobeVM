import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const source = fs.readFileSync(new URL('../src/pages/VMManager.jsx', import.meta.url), 'utf8')

test('remote management reads host-qualified live settings', () => {
  assert.match(source, /vm-settings\/\$\{encodeURIComponent\(name\)\}\$\{query\}/)
  assert.match(source, /hostId && hostId !== 'local' \? `\?host_id=/)
  assert.match(source, /Live RemoteVM state/)
  assert.match(source, /provider_status/)
})

test('remote management lifecycle controls keep the selected host', () => {
  assert.match(source, /manageLifecycle\('start'\)/)
  assert.match(source, /manageLifecycle\('stop'\)/)
  assert.match(source, /manageLifecycle\('restart'\)/)
  assert.match(source, /action\(cmd, name, \{ hostId \}\)/)
  assert.match(source, /actionParams\.set\('host_id', hostId\)/)
})

test('remote start and restart reconcile Moonlight before releasing the action', () => {
  assert.match(source, /if\(isRemote && \(cmd === 'start' \|\| cmd === 'restart'\)\)/)
  assert.match(source, /await reconcileRemoteConsole\(name, hostId\)/)
  assert.match(source, /remote console is still recovering/)
})

test('remote console opens a warmup route before the Moonlight route is verified', () => {
  assert.match(source, /consoleLaunchable = isRemote \? \(vm\.running === true && !!vm\.url\)/)
  assert.match(source, /consoleRouteReady !== false/)
  assert.match(source, /opening its retry page/)
})
