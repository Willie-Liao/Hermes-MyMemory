import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  formatEventBodyFromSlots,
  parseEventBodySlots,
} from './weeklyReviewOps.ts';

describe('event body slots', () => {
  it('parses semicolon digest bodies without putting labels in slot values', () => {
    const slots = parseEventBodySlots(
      'Beginning: asked; Course: traced; Outcome: recalled.',
    );
    assert.equal(slots.beginning, 'asked');
    assert.equal(slots.course, 'traced');
    assert.equal(slots.outcome, 'recalled.');
    assert.equal(slots.beginning.includes('Beginning:'), false);
    assert.equal(slots.course.includes('Course:'), false);
    assert.equal(slots.outcome.includes('Outcome:'), false);
  });

  it('parses newline event bodies', () => {
    const slots = parseEventBodySlots(
      'Beginning: parent photos.\nCourse: parent photos.\nOutcome: parent photos.',
    );
    assert.equal(slots.beginning, 'parent photos.');
    assert.equal(slots.course, 'parent photos.');
    assert.equal(slots.outcome, 'parent photos.');
  });

  it('round-trips slots through the digest join so labels stay in code', () => {
    const body = formatEventBodyFromSlots({
      beginning: 'asked',
      course: 'ran digest',
      outcome: 'shipped',
    });
    assert.equal(body, 'Beginning: asked; Course: ran digest; Outcome: shipped');
    const again = parseEventBodySlots(body);
    assert.deepEqual(again, {
      beginning: 'asked',
      course: 'ran digest',
      outcome: 'shipped',
    });
  });

  it('keeps unparsed prose in beginning so the other two slots stay empty', () => {
    const slots = parseEventBodySlots('freeform note without prefixes');
    assert.equal(slots.beginning, 'freeform note without prefixes');
    assert.equal(slots.course, '');
    assert.equal(slots.outcome, '');
  });
});
