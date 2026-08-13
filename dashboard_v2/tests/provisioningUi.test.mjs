import test from 'node:test'
import assert from 'node:assert/strict'
import { canClaimProvisioningJob, canOpenInventoryVm, canOpenProvisionedVm, canRetryProvisioningConsole, deprovisioningPayload, provisioningClaimPayload, provisioningConsoleRetryPayload, provisioningFailureReason, provisioningProgress } from '../src/lib/provisioningUi.js'

test('claim and open gates are state-specific', () => {
  assert.equal(canClaimProvisioningJob({ state:'unclaimed' }), true)
  assert.equal(canClaimProvisioningJob({ state:'ready' }), false)
  assert.equal(canOpenProvisionedVm({ state:'ready' }), true)
  assert.equal(canOpenProvisionedVm({ state:'verifying' }), false)
  assert.equal(canOpenInventoryVm({ provisioningState:'guest_setup' }), false)
  assert.equal(canOpenInventoryVm({ provisioningState:'ready' }), true)
  assert.equal(canRetryProvisioningConsole({ state:'setup_failed:streaming' }), true)
})

test('progress is monotonic across the approved state sequence', () => {
  assert.equal(provisioningProgress({ state:'queued' }), 0)
  assert.equal(provisioningProgress({ state:'unclaimed' }) > provisioningProgress({ state:'cloning' }), true)
  assert.equal(provisioningProgress({ state:'ready' }), 100)
})

test('claim payload keeps the token in the request boundary and teardown requires exact name', () => {
  assert.deepEqual(provisioningClaimPayload({ hostId:'epic-pc', username:'operator', password:'transient-password', claimToken:'one-use' }), {
    host_id:'epic-pc', username:'operator', password:'transient-password', claimToken:'one-use'
  })
  assert.deepEqual(deprovisioningPayload({ hostId:'epic-pc', name:' Alpha ' }), { host_id:'epic-pc', name:'alpha', confirmName:'alpha' })
  assert.deepEqual(provisioningConsoleRetryPayload({ hostId:'epic-pc', username:'operator', password:'transient-password' }), {
    host_id:'epic-pc', username:'operator', password:'transient-password'
  })
})

test('safe provisioning failure codes explain the failed trust boundary', () => {
  assert.equal(provisioningFailureReason({ errorCode:'powershell_direct_failed' }), 'PowerShell Direct could not open the cloned guest.')
  assert.equal(provisioningFailureReason({ errorCode:'rdp_verification_failed' }), 'Guest RDP/NLA/firewall verification failed.')
  assert.equal(provisioningFailureReason({ errorCode:'guest_account_failed' }), 'Windows guest-account setup failed after the claim was consumed.')
  assert.equal(provisioningFailureReason({ errorCode:'secret_leaked' }), '')
})
