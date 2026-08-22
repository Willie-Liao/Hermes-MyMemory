import assert from 'node:assert/strict';
import { nextChronicleFoldedOnTabChange } from './chronicleFold.ts';

// Approval Hub → expanded by default (folded = false)
assert.equal(nextChronicleFoldedOnTabChange('approve', true), false);
assert.equal(nextChronicleFoldedOnTabChange('approve', false), false);

// Leave Approval Hub while expanded → collapse
assert.equal(nextChronicleFoldedOnTabChange('read', false), true);
assert.equal(nextChronicleFoldedOnTabChange('hot', false), true);

// Already collapsed on read/hot → stay collapsed
assert.equal(nextChronicleFoldedOnTabChange('read', true), true);
assert.equal(nextChronicleFoldedOnTabChange('hot', true), true);

// Switch read↔hot while manually expanded → collapse again
assert.equal(nextChronicleFoldedOnTabChange('hot', false), true);
assert.equal(nextChronicleFoldedOnTabChange('read', false), true);

console.log('chronicleFold.test.ts: ok');
