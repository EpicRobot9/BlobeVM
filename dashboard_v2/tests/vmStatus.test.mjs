import test from 'node:test'
import assert from 'node:assert/strict'
import { normalizeVmStatus } from '../src/lib/vmStatus.js'

test('remote inventory status follows the Hyper-V VM state', () => {
  const vm = normalizeVmStatus({ name: 'alpha', state: 'Off', status: 'Operating normally' })
  assert.equal(vm.state, 'Off')
  assert.equal(vm.status, 'Off')
  assert.equal(vm.running, false)
})

test('running remote inventory stays live even when provider health is generic', () => {
  const vm = normalizeVmStatus({ name: 'alpha', state: 'Running', status: 'Operating normally' })
  assert.equal(vm.state, 'Running')
  assert.equal(vm.status, 'Running')
  assert.equal(vm.running, true)
})

test('generic provider health is not presented as VM state', () => {
  const vm = normalizeVmStatus({ name: 'alpha', status: 'Operating normally' })
  assert.equal(vm.state, 'Unknown')
  assert.equal(vm.status, 'Unknown')
  assert.equal(vm.provider_status, 'Operating normally')
  assert.equal(vm.running, false)
})
