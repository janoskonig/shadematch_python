// main.js (Mixbox JS + Flask colormath backend)

import { startTimer, stopTimer, resetTimerDisplay } from './timer.js';

console.log("✅ main.js loaded");
let sessionLogs = [];

// Store user ID globally for this session
window.currentUserId = localStorage.getItem('userId');

// Listen for user ID changes
window.addEventListener('storage', (e) => {
  if (e.key === 'userId') {
    window.currentUserId = e.newValue;
    // Reset the session when user changes
    resetMix();
    resetTimerDisplay();
    document.getElementById("startBtn").disabled = false;
    document.getElementById("stopBtn").disabled = true;
    document.getElementById("skipBtn").disabled = true;
    document.getElementById("restartBtn").disabled = true;
    document.getElementById("retryBtn").disabled = true;
  }
});

function updateBox(id, rgb) {
  console.log(`updateBox(${id}, [${rgb}])`);
  const el = document.getElementById(id);
  el.style.backgroundColor = `rgb(${rgb.join(',')})`;
}

function saveSessionToServer(session) {
  fetch('/save_session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(session)
  })
  .then(res => res.json())
  .then(data => {
    if (data.status !== 'success') {
      alert('Failed to save session!');
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  const baseColors = {
    white: [255, 255, 255],
    black: [0, 0, 0],
    red: [255, 0, 0],
    yellow: [255, 255, 0],
    blue: [0, 0, 255]
  };

  const targetColors = [
    [255, 102, 30],    // Orange
    [113, 1, 105],     // Purple
    [78, 150, 100],    // Green
    [255, 179, 188],   // Pink
    [128, 128, 0],     // Olive
    [98, 135, 96],     // Custom
    [255, 229, 180],   // Peach
    [255, 128, 80],    // Coral
    [64, 224, 208],    // Turquoise
    [128, 255, 0],     // Chartreuse
    [0, 128, 128]      // Teal
  ];

  let dropCounts = {
    white: 0,
    black: 0,
    red: 0,
    yellow: 0,
    blue: 0
  };

  let currentTargetIndex = 0;
  let targetColor = targetColors[currentTargetIndex];

  function resetMix() {
    for (let key in dropCounts) dropCounts[key] = 0;
    document.querySelectorAll(".color-circle").forEach(c => c.textContent = "0");
    updateBox("currentMix", [255, 255, 255]);
    document.getElementById("deltaE").textContent = "-";
  }

  function updateCurrentMix() {
    const totalDrops = Object.values(dropCounts).reduce((a, b) => a + b, 0);
    if (totalDrops === 0) {
      updateBox("currentMix", [255, 255, 255]);
      document.getElementById("deltaE").textContent = "-";
      return;
    }

    let zMix = new Array(mixbox.LATENT_SIZE).fill(0);

    for (let color in dropCounts) {
      const count = dropCounts[color];
      if (count > 0) {
        const [r, g, b] = baseColors[color];
        const z = mixbox.rgbToLatent(r, g, b);
        for (let i = 0; i < zMix.length; i++) {
          zMix[i] += (count / totalDrops) * z[i];
        }
      }
    }

    const mixedRGB = mixbox.latentToRgb(zMix).map(Math.round);
    console.log("🎨 Mixed RGB:", mixedRGB);
    updateBox("currentMix", mixedRGB);

    fetch("/calculate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target: targetColor,
        mixed: mixedRGB
      })
    })
    .then(res => res.json())
    .then(data => {
      if (data.error) return console.error("Server error:", data.error);
      document.getElementById("deltaE").textContent = data.delta_e.toFixed(2);

      if (data.delta_e < 5) {
        const session = {
          userId: window.currentUserId,
          target: targetColor,
          drops: { ...dropCounts },
          deltaE: data.delta_e,
          time: parseFloat(document.getElementById("timer").textContent),
          timestamp: new Date().toISOString()
        };
        sessionLogs.push(session);
        saveSessionToServer(session);

        setTimeout(() => {
          alert(`✅ Matched with ΔE=${data.delta_e.toFixed(2)}!`);
          currentTargetIndex++;
          if (currentTargetIndex < targetColors.length) {
            targetColor = targetColors[currentTargetIndex];
            updateBox("targetColor", targetColor);
            resetMix();
            startTimer();
          } else {
            alert("🎉 All colors completed!");
          }
        }, 300);
      }
    });
  }

  // Button logic
  document.getElementById("startBtn").addEventListener("click", () => {
    currentTargetIndex = 0;
    targetColor = targetColors[currentTargetIndex];
    updateBox("targetColor", targetColor);
    resetMix();
    startTimer();

    document.getElementById("stopBtn").disabled = false;
    document.getElementById("startBtn").disabled = true;
    document.getElementById("restartBtn").disabled = false;
    document.getElementById("retryBtn").disabled = false;
  });

  document.getElementById("stopBtn").addEventListener("click", () => {
    stopTimer();
    const session = {
      userId: window.currentUserId,
      target: targetColor,
      drops: { ...dropCounts },
      deltaE: parseFloat(document.getElementById("deltaE").textContent),
      time: parseFloat(document.getElementById("timer").textContent),
      timestamp: new Date().toISOString()
    };
    sessionLogs.push(session);
    saveSessionToServer(session);
    document.getElementById("skipBtn").disabled = false;
    document.getElementById("stopBtn").disabled = true;
  });

  document.getElementById("skipBtn").addEventListener("click", () => {
    currentTargetIndex++;
    if (currentTargetIndex < targetColors.length) {
      targetColor = targetColors[currentTargetIndex];
      updateBox("targetColor", targetColor);
      resetMix();
      startTimer();
      document.getElementById("skipBtn").disabled = true;
      document.getElementById("stopBtn").disabled = false;
    } else {
      alert("✅ All colors completed!");
    }
  });

  document.getElementById("restartBtn").addEventListener("click", () => {
    currentTargetIndex = 0;
    targetColor = targetColors[currentTargetIndex];
    updateBox("targetColor", targetColor);
    resetMix();
    resetTimerDisplay();
    startTimer();
    document.getElementById("stopBtn").disabled = false;
    document.getElementById("skipBtn").disabled = true;
  });

  document.getElementById("retryBtn").addEventListener("click", () => {
    resetMix();
    resetTimerDisplay();
    startTimer();
    document.getElementById("stopBtn").disabled = false;
    document.getElementById("skipBtn").disabled = true;
  });

  document.querySelectorAll(".color-circle").forEach(circle => {
    circle.addEventListener("click", () => {
      const color = circle.dataset.color;
      dropCounts[color]++;
      circle.textContent = dropCounts[color];
      updateCurrentMix();
    });
  });

  document.querySelectorAll(".minus-button").forEach(button => {
    button.addEventListener("click", () => {
      const color = button.dataset.color;
      if (dropCounts[color] > 0) {
        dropCounts[color]--;
        document.querySelector(`.color-circle[data-color='${color}']`).textContent = dropCounts[color];
        updateCurrentMix();
      }
    });
  });

  // Optionally, you can comment out or remove the CSV export logic below if you no longer want to support CSV export.
  // document.getElementById("exportBtn").addEventListener("click", () => {
  //   const csvRows = [];
  //   csvRows.push([
  //     "user_id",
  //     "target_r", "target_g", "target_b",
  //     "drop_white", "drop_black", "drop_red", "drop_yellow", "drop_blue",
  //     "delta_e", "time_sec", "timestamp"
  //   ].join(","));
  //
  //   sessionLogs.forEach(entry => {
  //     const [r, g, b] = entry.target;
  //     const d = entry.drops;
  //     const line = [
  //       entry.userId || '',
  //       r, g, b,
  //       d.white || 0,
  //       d.black || 0,
  //       d.red || 0,
  //       d.yellow || 0,
  //       d.blue || 0,
  //       entry.deltaE !== null ? entry.deltaE.toFixed(2) : "",
  //       entry.time.toFixed(1),
  //       entry.timestamp
  //     ];
  //     csvRows.push(line.join(","));
  //   });
  //
  //   const csvContent = "data:text/csv;charset=utf-8," + csvRows.join("\n");
  //   const encodedUri = encodeURI(csvContent);
  //   const link = document.createElement("a");
  //   link.setAttribute("href", encodedUri);
  //   link.setAttribute("download", "color_matching_log.csv");
  //   document.body.appendChild(link);
  //   link.click();
  //   document.body.removeChild(link);
  // });
});
