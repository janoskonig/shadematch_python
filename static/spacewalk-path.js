// Shortest paths in the nonnegative five-dimensional, unit-drop recipe graph.
(function (global) {
  'use strict';
  function shortestRecipePath(start, target) {
    for (const vector of [start, target]) {
      if (!Array.isArray(vector) || vector.length !== 5 ||
          !vector.every(value => Number.isSafeInteger(value) && value >= 0)) {
        throw new RangeError('A recipe must contain five nonnegative integer drop counts.');
      }
    }
    const differences = target.map((value, j) => value - start[j]);
    const required = differences.map(Math.abs);
    const steps = required.reduce((sum, value) => sum + value, 0);
    const completed = [0, 0, 0, 0, 0], current = [...start], path = [[...start]];
    for (let step = 1; step <= steps; step++) {
      // Interleave the required operations near proportional progress. Ties use
      // pigment order. Every operation reduces the remaining L1 distance by one,
      // so this constructs one shortest path without searching an infinite graph.
      let next = -1, largestLag = -Infinity;
      for (let j = 0; j < 5; j++) {
        if (completed[j] === required[j]) continue;
        const lag = required[j] * step / steps - completed[j];
        if (lag > largestLag) { next = j; largestLag = lag; }
      }
      current[next] += Math.sign(differences[next]);
      completed[next]++;
      path.push([...current]);
    }
    return path;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { shortestRecipePath };
  else global.ShadeRecipePath = { shortestRecipePath };
})(typeof window !== 'undefined' ? window : globalThis);
