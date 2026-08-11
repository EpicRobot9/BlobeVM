import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const source = fs.readFileSync(new URL('../src/pages/VMManager.jsx', import.meta.url), 'utf8')

test('VM manager exposes authenticated RemoteVM token-file enrollment', () => {
  assert.match(source, /hosts\/enroll/)
  assert.match(source, /FormData/)
  assert.match(source, /token_file/)
  assert.match(source, /RemoteVM host/)
  assert.equal(source.includes('pattern="[a-z0-9][a-z0-9._\\-]{0,62}"'), true)
  // The one-time provisioning claim may live in React memory while the form
  // is open, but it must never be persisted in browser storage.
  assert.doesNotMatch(source, /localStorage\.[^\n]*token/i)
  assert.doesNotMatch(source, /sessionStorage\.[^\n]*token/i)
})

test('claim and console retry buttons do not create nested forms', () => {
  assert.doesNotMatch(source, /<form onSubmit=\{claimProvisioningJob\}/)
  assert.doesNotMatch(source, /<form onSubmit=\{retryProvisioningConsole\}/)
  assert.match(source, /type="button" onClick=\{claimProvisioningJob\}/)
  assert.match(source, /type="button" onClick=\{retryProvisioningConsole\}/)
})
