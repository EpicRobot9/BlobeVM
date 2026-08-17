import test from 'node:test'
import assert from 'node:assert/strict'
import { instanceNamesKey, pollDelayMs } from '../src/lib/polling.js'

test('instanceNamesKey is stable for the same instance-name set', () => {
  assert.equal(
    instanceNamesKey([{ name: 'beta' }, { name: 'alpha' }]),
    instanceNamesKey([{ name: 'alpha' }, { name: 'beta' }])
  )
})

test('instanceNamesKey changes when an instance is added or removed', () => {
  const initial = instanceNamesKey([{ name: 'alpha' }])
  assert.notEqual(initial, instanceNamesKey([{ name: 'alpha' }, { name: 'beta' }]))
  assert.notEqual(initial, instanceNamesKey([]))
})

test('instanceNamesKey distinguishes the same VM name on different hosts', () => {
  assert.notEqual(
    instanceNamesKey([{ name: 'alpha', host_id: 'local' }]),
    instanceNamesKey([{ name: 'alpha', host_id: 'epic-pc' }])
  )
})
test('pollDelayMs clamps visible polling to the existing minimum', () => {
  assert.equal(pollDelayMs({ visible: true, intervalMs: 100 }), 800)
  assert.equal(pollDelayMs({ visible: true, intervalMs: 3000 }), 3000)
})

test('pollDelayMs uses a conservative delay while hidden', () => {
  assert.equal(pollDelayMs({ visible: false, intervalMs: 3000, hiddenDelayMs: 60000 }), 60000)
  assert.equal(pollDelayMs({ visible: false, intervalMs: 3000 }), 60000)
})
