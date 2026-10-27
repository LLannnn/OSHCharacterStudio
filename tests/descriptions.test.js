// Exercise the actual UI autofill function with minimal DOM fields. No network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
const autofill = source.match(/function fillTrackingDescriptions\(\)\{[\s\S]*?\n\}/)[0];
function fill(current, dirty = false, initial = {}) {
  const fields = {trackingDescriptionA: {value: initial.A || ''}, trackingDescriptionB: {value: initial.B || ''}};
  const context = {current, descriptionsDirty: dirty, $: id => fields[id]};
  vm.runInNewContext(autofill + '\nfillTrackingDescriptions();', context);
  return {A: fields.trackingDescriptionA.value, B: fields.trackingDescriptionB.value};
}
assert.deepEqual(fill({facts: {}}), {A: '', B: ''});
const current = {prepared: {id: 'current'}, facts: {source_A: 'woman in pink', source_B: 'man in patterned pink'}};
assert.deepEqual(fill(current), {A: 'woman in pink', B: 'man in patterned pink'});
current.tracking_descriptions = {checkpoint_id: 'current', values: {A: '  ', B: 'custom source B'}};
assert.deepEqual(fill(current), {A: 'woman in pink', B: 'custom source B'});
current.tracking_descriptions.checkpoint_id = 'old';
assert.deepEqual(fill(current), {A: 'woman in pink', B: 'man in patterned pink'});
assert.deepEqual(fill(current, true, {A: 'unsaved A', B: 'unsaved B'}), {A: 'unsaved A', B: 'unsaved B'});
console.log('Description autofill passed: fresh project, saved facts, blank fallback, stale checkpoint and unsaved edits.');
