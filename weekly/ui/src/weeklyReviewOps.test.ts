import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  formatDecisionBodyFromSlots,
  formatEventBodyFromSlots,
  formatFactBodyFromSlots,
  formatProcedureBodyFromSlots,
  parseDecisionBodySlots,
  parseEventBodySlots,
  parseFactBodySlots,
  parseProcedureBodySlots,
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

describe('procedure body slots', () => {
  it('parses semicolon digest bodies', () => {
    const slots = parseProcedureBodySlots(
      'Obstacle: lock was stale; Solution: clear the lock then restart.',
    );
    assert.equal(slots.obstacle, 'lock was stale');
    assert.equal(slots.solution, 'clear the lock then restart.');
  });

  it('round-trips through the digest join', () => {
    const body = formatProcedureBodyFromSlots({
      obstacle: 'jam',
      solution: 'use plain paper',
    });
    assert.equal(body, 'Obstacle: jam; Solution: use plain paper');
    assert.deepEqual(parseProcedureBodySlots(body), {
      obstacle: 'jam',
      solution: 'use plain paper',
    });
  });
});

describe('fact body slots', () => {
  it('parses Factual and Narration prefixes', () => {
    const factual = parseFactBodySlots('Factual: Alice lives in HK');
    assert.equal(factual.kind, 'Factual');
    assert.equal(factual.content, 'Alice lives in HK');
    const narration = parseFactBodySlots('Narration: they walked by the lake');
    assert.equal(narration.kind, 'Narration');
    assert.equal(narration.content, 'they walked by the lake');
  });

  it('round-trips through the digest join', () => {
    const body = formatFactBodyFromSlots({
      kind: 'Narration',
      content: 'cast shared a meal',
    });
    assert.equal(body, 'Narration: cast shared a meal');
    assert.deepEqual(parseFactBodySlots(body), {
      kind: 'Narration',
      content: 'cast shared a meal',
    });
  });
});

describe('decision body slots', () => {
  it('parses Preference and Decision prefixes', () => {
    const pref = parseDecisionBodySlots(
      'Preference: user prefers unobscured screenshots',
    );
    assert.equal(pref.kind, 'Preference');
    assert.equal(pref.subject, 'user');
    assert.equal(pref.ruling, 'prefers unobscured screenshots');
    const dec = parseDecisionBodySlots(
      'Decision: user must enable embedding-based semantic recall',
    );
    assert.equal(dec.kind, 'Decision');
    assert.equal(dec.subject, 'user');
    assert.equal(dec.ruling, 'must enable embedding-based semantic recall');
  });

  it('round-trips through the digest join', () => {
    const body = formatDecisionBodyFromSlots({
      kind: 'Preference',
      subject: 'user',
      ruling: 'must keep bodies prefixed',
    });
    assert.equal(body, 'Preference: user must keep bodies prefixed');
    assert.deepEqual(parseDecisionBodySlots(body), {
      kind: 'Preference',
      subject: 'user',
      ruling: 'must keep bodies prefixed',
    });
  });
});
