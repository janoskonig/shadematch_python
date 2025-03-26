// main.js (Mixbox JS + Flask colormath backend)

console.log("✅ main.js loaded");

function updateBox(id, rgb) {
  console.log(`updateBox(${id}, [${rgb}])`);
  const el = document.getElementById(id);
  el.style.backgroundColor = `rgb(${rgb.join(',')})`;
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
    [113, 1, 105],    // Purple
    [78, 150, 100],      // Green
    [255, 179, 188],  // Pink
    [128, 128, 0],    // Olive
    [98, 135, 96],      // Maroon
    [255, 229, 180],  // Peach
    [255, 128, 80],   // Coral
    [64, 224, 208],   // Turquoise
    [128, 255, 0],    // Chartreuse
    [0, 128, 128]     // Teal
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
  updateBox("targetColor", targetColor);

    // Handle add (click on color circle)
    document.querySelectorAll(".color-circle").forEach(circle => {
    circle.addEventListener("click", () => {
      const color = circle.dataset.color;
      dropCounts[color]++;
      circle.textContent = dropCounts[color];
      updateCurrentMix();
    });
  });
  
  // Handle subtract (click on minus button)
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

    // 🧪 Mix with Mixbox in JS
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

    // 🧪 Send to backend for ΔE calculation
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
          setTimeout(() => {
            alert(`✅ Matched with ΔE=${data.delta_e.toFixed(2)}!`);
            currentTargetIndex++;
            if (currentTargetIndex < targetColors.length) {
              targetColor = targetColors[currentTargetIndex];
              updateBox("targetColor", targetColor);
              resetMix();
            } else {
              alert("🎉 All colors completed!");
            }
          }, 300);
        }
      });
  }
});
