const assert = require('node:assert/strict');
const { test } = require('node:test');
const { shortestRecipePath } = require('../static/spacewalk-path.js');

function checkPath(start, target, expectedSteps) {
  const savedStart = [...start], savedTarget = [...target];
  const path = shortestRecipePath(start, target);
  assert.deepEqual(path[0], start);
  assert.deepEqual(path.at(-1), target);
  assert.equal(path.length - 1, expectedSteps);
  for (let i = 1; i < path.length; i++) {
    assert(path[i].every(value => Number.isInteger(value) && value >= 0));
    const changes = path[i].map((value, j) => value - path[i - 1][j]);
    assert.equal(changes.filter(value => value !== 0).length, 1);
    assert.equal(changes.reduce((sum, value) => sum + Math.abs(value), 0), 1);
  }
  assert.deepEqual(start, savedStart);
  assert.deepEqual(target, savedTarget);
  return path;
}

test('the opening Pizza example takes 25 unit-drop moves after the first addition', () => {
  checkPath([0, 0, 0, 1, 0], [3, 5, 5, 13, 0], 25);
});

test('paths include removals, empty nodes and zero-distance endpoints', () => {
  checkPath([3, 1, 4, 0, 2], [1, 5, 2, 3, 0], 13);
  checkPath([1, 0, 0, 0, 0], [0, 0, 0, 0, 1], 2);
  checkPath([0, 0, 0, 0, 0], [0, 0, 0, 0, 0], 0);
  checkPath([0, 2, 1, 0, 3], [0, 2, 1, 0, 3], 0);
});

test('all pairs in a small 5D graph match independent breadth-first search', () => {
  const vertices = Array.from({length: 32}, (_, mask) =>
    Array.from({length: 5}, (_, j) => (mask >> j) & 1));
  for (let start = 0; start < vertices.length; start++) {
    const distance = new Map([[start, 0]]), queue = [start];
    for (let i = 0; i < queue.length; i++) {
      for (let j = 0; j < 5; j++) {
        const next = queue[i] ^ (1 << j);
        if (!distance.has(next)) {
          distance.set(next, distance.get(queue[i]) + 1);
          queue.push(next);
        }
      }
    }
    for (let end = 0; end < vertices.length; end++) {
      checkPath(vertices[start], vertices[end], distance.get(end));
    }
  }
});

test('invalid drop counts cannot create invalid graph nodes', () => {
  for (const invalid of [[-1, 0, 0, 0, 0], [0.5, 0, 0, 0, 0], [0, 0], [NaN, 0, 0, 0, 0]]) {
    assert.throws(() => shortestRecipePath(invalid, [0, 0, 0, 0, 0]), RangeError);
  }
});
