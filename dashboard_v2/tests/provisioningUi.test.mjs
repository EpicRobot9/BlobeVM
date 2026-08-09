import test from 'node:test'
import assert from 'node:assert/strict'
import { canClaimProvisioningJob, canOpenInventoryVm, canOpenProvisionedVm, deprovisioningPayload, provisioningClaimPayload, provisioningProgress } from '../src/lib/provisioningUi.js'

test('claim and open gates are state-specific', () => {
  assert.equal(canClaimProvisioningJob({ state:'awaiting_claim' }), true)
  assert.equal(canClaimProvisioningJob({ state:'ready' }), false)
  assert.equal(canOpenProvisionedVm({ state:'ready' }), true)
  assert.equal(canOpenProvisionedVm({ state:'verifying' }), false)
  assert.equal(canOpenInventoryVm({ provisioningState:'configuring_guest' }), false)
  assert.equal(canOpenInventoryVm({ provisioningState:'ready' }), true)
})

test('progress is monotonic across the approved state sequence', () => {
  assert.equal(provisioningProgress({ state:'queued' }), 0)
  assert.equal(provisioningProgress({ state:'awaiting_claim' }) > provisioningProgress({ state:'cloning' }), true)
  assert.equal(provisioningProgress({ state:'ready' }), 100)
})

test('claim payload keeps the token in the request boundary and teardown requires exact name', () => {
  assert.deepEqual(provisioningClaimPayload({ hostId:'epic-pc', username:'operator', password:'transient-password', claimToken:'one-use' }), {
    host_id:'epic-pc', username:'operator', password:'transient-password', claimToken:'one-use'
  })
  assert.deepEqual(deprovisioningPayload({ hostId:'epic-pc', name:' Alpha ' }), { host_id:'epic-pc', name:'alpha', confirmName:'alpha' })
})
