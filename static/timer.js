let timer = null;
let startTime = null;
let timerInterval = null;

export function startTimer() {
  if (timerInterval) return;
  startTime = Date.now();
  timerInterval = setInterval(() => {
    const elapsed = (Date.now() - startTime) / 1000;
    document.getElementById("timer").textContent = elapsed.toFixed(1);
  }, 100);
}

export function stopTimer() {
  clearInterval(timerInterval);
  timerInterval = null;
}

export function resetTimerDisplay() {
  document.getElementById("timer").textContent = "0.0";
}