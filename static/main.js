// main.js (Mixbox JS + Flask colormath backend)

import { startTimer, stopTimer, resetTimerDisplay } from './timer.js';

console.log("✅ main.js loaded");
let sessionLogs = [];
let currentSessionSaved = false; // Flag to prevent duplicate saves

// Cookie Consent Integration Example
// This shows how to use the cookie consent system in your application
document.addEventListener('DOMContentLoaded', function() {
    // Wait for cookie consent to be initialized
    setTimeout(() => {
        if (window.cookieConsent) {
            console.log("🍪 Cookie consent system loaded");
            
            // Example: Only track analytics if user has consented
            if (window.cookieConsent.canUseAnalytics()) {
                console.log("📊 Analytics cookies enabled - tracking user interactions");
                // Here you would initialize Google Analytics or other tracking
                // gtag('config', 'GA_MEASUREMENT_ID');
            } else {
                console.log("📊 Analytics cookies disabled - respecting user privacy");
            }
            
            // Example: Only save preferences if user has consented
            if (window.cookieConsent.canUsePreferences()) {
                console.log("⚙️ Preference cookies enabled - saving user settings");
                // Here you would save user preferences
            } else {
                console.log("⚙️ Preference cookies disabled - not saving preferences");
            }
            
            // Listen for consent changes
            document.addEventListener('cookieConsentUpdated', function(event) {
                const consent = event.detail;
                console.log("🍪 Cookie consent updated:", consent);
                
                // Update services based on new consent
                if (consent.categories.analytics) {
                    console.log("📊 Enabling analytics tracking");
                    // Enable analytics
                } else {
                    console.log("📊 Disabling analytics tracking");
                    // Disable analytics
                }
            });
        }
    }, 1000);
});

// Store user ID globally for this session
window.currentUserId = localStorage.getItem('userId');

// Function to disable color mixing functionality
function disableColorMixing() {
  // Hide the target color and current mix display
  const targetColorElement = document.getElementById("targetColor");
  const currentMixElement = document.getElementById("currentMix");
  if (targetColorElement) targetColorElement.style.display = "none";
  if (currentMixElement) currentMixElement.style.display = "none";
  
  // Hide the palette with color controls
  const paletteElement = document.getElementById("palette");
  if (paletteElement) paletteElement.style.display = "none";
  
  // Disable all color circle click events
  document.querySelectorAll(".color-circle").forEach(circle => {
    circle.style.pointerEvents = "none";
    circle.style.opacity = "0.5";
  });
  
  // Disable all minus button click events
  document.querySelectorAll(".minus-button").forEach(button => {
    button.disabled = true;
    button.style.opacity = "0.5";
  });
}

// Function to enable color mixing functionality
function enableColorMixing() {
  // Show the target color and current mix display
  const targetColorElement = document.getElementById("targetColor");
  const currentMixElement = document.getElementById("currentMix");
  if (targetColorElement) targetColorElement.style.display = "";
  if (currentMixElement) currentMixElement.style.display = "";
  
  // Show the palette with color controls
  const paletteElement = document.getElementById("palette");
  if (paletteElement) paletteElement.style.display = "";
  
  // Enable all color circle click events
  document.querySelectorAll(".color-circle").forEach(circle => {
    circle.style.pointerEvents = "";
    circle.style.opacity = "";
  });
  
  // Enable all minus button click events
  document.querySelectorAll(".minus-button").forEach(button => {
    button.disabled = false;
    button.style.opacity = "";
  });
}
window.currentUserBirthdate = localStorage.getItem('userBirthdate');
window.currentUserGender = localStorage.getItem('userGender');
window.currentSessionSaved = false; // Make accessible to HTML template

// Define dropCounts globally
let dropCounts = {
  white: 0,
  black: 0,
  red: 0,
  yellow: 0,
  blue: 0
};

// Define resetMix function
function resetMix() {
  // Reset all drop counts to 0
  document.querySelectorAll('.color-circle').forEach(circle => {
    circle.textContent = '0';
  });
  
  // Reset the mixed color display to white
  document.getElementById('currentMix').style.backgroundColor = 'rgb(255, 255, 255)';
  document.getElementById('mixedRgbValues').textContent = 'RGB: [255, 255, 255]';
  document.getElementById('deltaE').textContent = '-';
  
  // Reset drop counts object
  dropCounts = {
    white: 0,
    black: 0,
    red: 0,
    yellow: 0,
    blue: 0
  };
  
  // Reset the session saved flag for the new color
  currentSessionSaved = false;
  window.currentSessionSaved = false; // Also update global reference
}

// Listen for user ID changes
window.addEventListener('storage', (e) => {
  if (e.key === 'userId') {
    window.currentUserId = e.newValue;
    window.currentUserBirthdate = localStorage.getItem('userBirthdate');
    window.currentUserGender = localStorage.getItem('userGender');
    // Reset the session when user changes
    resetMix();
    resetTimerDisplay();
    document.getElementById("startBtn").disabled = false;
    document.getElementById("stopBtn").disabled = true;
    document.getElementById("skipBtn").disabled = true;
    document.getElementById("restartBtn").disabled = true;
    document.getElementById("retryBtn").disabled = true;
    // Ensure color mixing is disabled when user changes until Start is clicked
    disableColorMixing();
  }
});

function updateBox(id, rgb) {
  console.log(`updateBox(${id}, [${rgb}])`);
  const el = document.getElementById(id);
  el.style.backgroundColor = `rgb(${rgb.join(',')})`;
}

function saveSessionToServer(session) {
  if (!window.currentUserId) {
    console.error('No user ID found');
    return;
  }

  console.log('Current user ID:', window.currentUserId);
  console.log('Session data:', session);

  const sessionData = {
    user_id: window.currentUserId,
    target_r: session.target[0],
    target_g: session.target[1],
    target_b: session.target[2],
    drop_white: session.drops.white,
    drop_black: session.drops.black,
    drop_red: session.drops.red,
    drop_yellow: session.drops.yellow,
    drop_blue: session.drops.blue,
    delta_e: session.deltaE,
    time_sec: session.time,
    timestamp: session.timestamp
  };

  console.log('Sending session data to server:', sessionData);

  fetch('/save_session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(sessionData)
  })
  .then(res => res.json())
  .then(data => {
    console.log('Server response:', data);
    if (data.status !== 'success') {
      console.error('Failed to save session:', data.error);
    }
  })
  .catch(error => {
    console.error('Error saving session:', error);
  });
}

document.addEventListener("DOMContentLoaded", () => {
  // Disable color mixing by default until Start button is clicked
  disableColorMixing();
  
  const baseColors = {
    white: [255, 255, 255],
    black: [0, 0, 0],
    red: [255, 0, 0],
    yellow: [255, 255, 0],
    blue: [0, 0, 255]
  };

  // Hard-coded target colors with skin color classifications
  const targetColors = [
    // Basic colors
    { name: 'Orange', type: 'basic', classification: null, rgb: [255, 102, 30] },
    { name: 'Purple', type: 'basic', classification: null, rgb: [113, 1, 105] },
    { name: 'Green', type: 'basic', classification: null, rgb: [78, 150, 100] },
    { name: 'Pink', type: 'basic', classification: null, rgb: [255, 179, 188] },
    { name: 'Olive', type: 'basic', classification: null, rgb: [113, 112, 62] },
    { name: 'Custom', type: 'basic', classification: null, rgb: [111, 122, 102] },
    { name: 'Peach', type: 'basic', classification: null, rgb: [255, 228, 175] },
    { name: 'Coral', type: 'basic', classification: null, rgb: [255, 131, 82] },
    { name: 'Turquoise', type: 'basic', classification: null, rgb: [103, 157, 174] },
    { name: 'Chartreuse', type: 'basic', classification: null, rgb: [157, 210, 103] },
    { name: 'Teal', type: 'basic', classification: null, rgb: [84, 122, 122] },
    
    // Skin colors - Light
    { name: '#D1AE90', type: 'skin', classification: 'skin_light', rgb: [208, 176, 148] },
    { name: '#AE967E', type: 'skin', classification: 'skin_light', rgb: [175, 149, 126] },
    { name: '#C3A28F', type: 'skin', classification: 'skin_light', rgb: [242, 166, 129] },
    { name: '#BE8870', type: 'skin', classification: 'skin_light', rgb: [193, 135, 115] },
    { name: '#6D544D', type: 'skin', classification: 'skin_light', rgb: [178, 125, 107] },
    { name: '#34261B', type: 'skin', classification: 'skin_light', rgb: [205, 87, 91] },
    { name: '#C8AF91', type: 'skin', classification: 'skin_light', rgb: [208, 176, 148] },
    { name: '#A97367', type: 'skin', classification: 'skin_light', rgb: [172, 115, 104] },
    { name: '#CB9781', type: 'skin', classification: 'skin_light', rgb: [212, 147, 125] },
    { name: '#B68678', type: 'skin', classification: 'skin_light', rgb: [193, 135, 115] },
    { name: '#E8B7BA', type: 'skin', classification: 'skin_light', rgb: [228, 183, 190] },
    { name: '#A58F5E', type: 'skin', classification: 'skin_light', rgb: [167, 145, 92] },
    { name: '#B5866A', type: 'skin', classification: 'skin_light', rgb: [180, 134, 106] },
    { name: '#DE958F', type: 'skin', classification: 'skin_light', rgb: [225, 155, 151] },
    
    // Skin colors - Dark
    { name: '#99856A', type: 'skin', classification: 'skin_dark', rgb: [155, 131, 108] },
    { name: '#A8856F', type: 'skin', classification: 'skin_dark', rgb: [182, 137, 96] },
    { name: '#A07E63', type: 'skin', classification: 'skin_dark', rgb: [169, 120, 74] },
    { name: '#80685C', type: 'skin', classification: 'skin_dark', rgb: [143, 103, 88] },
    { name: '#584B42', type: 'skin', classification: 'skin_dark', rgb: [88, 71, 52] },
    { name: '#7B5749', type: 'skin', classification: 'skin_dark', rgb: [127, 84, 67] },
    { name: '#543B34', type: 'skin', classification: 'skin_dark', rgb: [174, 121, 123] },
    { name: '#583E2D', type: 'skin', classification: 'skin_dark', rgb: [80, 62, 41] },
    { name: '#A76662', type: 'skin', classification: 'skin_dark', rgb: [161, 104, 98] },
    { name: '#A28074', type: 'skin', classification: 'skin_dark', rgb: [165, 130, 118] },
    { name: '#8F7868', type: 'skin', classification: 'skin_dark', rgb: [144, 121, 101] },
    { name: '#9F7954', type: 'skin', classification: 'skin_dark', rgb: [189, 131, 76] },
    { name: '#392D1D', type: 'skin', classification: 'skin_dark', rgb: [57, 42, 22] },
    { name: '#9D7248', type: 'skin', classification: 'skin_dark', rgb: [150, 114, 71] },
    { name: '#58482F', type: 'skin', classification: 'skin_dark', rgb: [88, 68, 44] }
  ];

  let currentTargetIndex = 0;
  let currentTargetColor = targetColors[0];
  let targetColor = currentTargetColor.rgb;

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
    
    // Update RGB values display
    document.getElementById("mixedRgbValues").textContent = `RGB: [${mixedRGB.join(', ')}]`;

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

      if (data.delta_e === 0.00) {
        stopTimer();
        const session = {
          user_id: window.currentUserId,
          target: targetColor,
          drops: { ...dropCounts },
          deltaE: data.delta_e,
          time: parseFloat(document.getElementById("timer").textContent),
          timestamp: new Date().toISOString()
        };
        sessionLogs.push(session);
        saveSessionToServer(session);
        currentSessionSaved = true; // Mark this session as saved
        window.currentSessionSaved = true; // Also update global reference

        const skipBtn = document.getElementById("skipBtn");
        skipBtn.disabled = false;
        skipBtn.textContent = "Next color";
      }
    });
  }

  // Button logic
  document.getElementById("startBtn").addEventListener("click", () => {
    currentTargetIndex = 0;
    currentTargetColor = targetColors[currentTargetIndex];
    targetColor = currentTargetColor.rgb;
    updateBox("targetColor", targetColor);
    resetMix();
    startTimer();

    // Enable color mixing functionality
    enableColorMixing();

    document.getElementById("stopBtn").disabled = false;
    document.getElementById("startBtn").disabled = true;
    document.getElementById("restartBtn").disabled = false;
    document.getElementById("retryBtn").disabled = false;
    document.getElementById("skipBtn").disabled = false;
  });

  document.getElementById("stopBtn").addEventListener("click", () => {
    stopTimer();
    const currentDeltaE = parseFloat(document.getElementById("deltaE").textContent);
    
    // Always save the current session data when Stop is clicked
    if (!isNaN(currentDeltaE)) {  // Only save if we have a valid DeltaE
      const session = {
        user_id: window.currentUserId,
        target: targetColor,
        drops: { ...dropCounts },
        deltaE: currentDeltaE,
        time: parseFloat(document.getElementById("timer").textContent),
        timestamp: new Date().toISOString()
      };
      sessionLogs.push(session);
      saveSessionToServer(session);
    }
    
    // Disable color mixing functionality
    disableColorMixing();
    
    document.getElementById("skipBtn").disabled = false;
    document.getElementById("stopBtn").disabled = true;
  });

  document.getElementById("skipBtn").addEventListener("click", () => {
    // Get current deltaE value
    const currentDeltaE = parseFloat(document.getElementById("deltaE").textContent);
    
    // Only save skip if this wasn't already saved as a successful completion
    // (i.e., if ΔE is not 0.00, meaning no automatic save happened)
    if (currentDeltaE !== 0.00) {
      const skipData = {
        user_id: window.currentUserId,
        target_r: targetColor[0],
        target_g: targetColor[1],
        target_b: targetColor[2],
        time_sec: parseFloat(document.getElementById("timer").textContent),
        timestamp: new Date().toISOString(),
        delta_e: isNaN(currentDeltaE) ? null : currentDeltaE
      };
      
      fetch('/save_skip', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(skipData)
      })
      .then(res => res.json())
      .then(data => {
        console.log('Skip saved to server:', data);
      })
      .catch(error => {
        console.error('Error saving skip:', error);
      });
    } else {
      console.log('Skip not saved - session already recorded as successful completion');
    }
    
    currentTargetIndex++;
    if (currentTargetIndex < targetColors.length) {
      currentTargetColor = targetColors[currentTargetIndex];
      targetColor = currentTargetColor.rgb;
      updateBox("targetColor", targetColor);
      resetMix();
      startTimer();
      document.getElementById("skipBtn").disabled = false;
      document.getElementById("stopBtn").disabled = false;
      document.getElementById("skipBtn").textContent = "Skip";
    } else {
      alert("✅ All colors completed!");
      document.getElementById("skipBtn").disabled = true;
      document.getElementById("stopBtn").disabled = true;
    }
  });

  document.getElementById("restartBtn").addEventListener("click", () => {
    currentTargetIndex = 0;
    currentTargetColor = targetColors[currentTargetIndex];
    targetColor = currentTargetColor.rgb;
    updateBox("targetColor", targetColor);
    resetMix();
    resetTimerDisplay();
    startTimer();
    
    // Enable color mixing functionality
    enableColorMixing();
    
    document.getElementById("stopBtn").disabled = false;
    document.getElementById("skipBtn").disabled = false;
    document.getElementById("skipBtn").disabled = true;
  });

  document.getElementById("retryBtn").addEventListener("click", () => {
    // Store current session data before resetting
    const currentDeltaE = parseFloat(document.getElementById("deltaE").textContent);
    if (!isNaN(currentDeltaE)) {  // Only store if we have a valid DeltaE
      const session = {
        user_id: window.currentUserId,
        target: targetColor,
        drops: { ...dropCounts },
        deltaE: currentDeltaE,
        time: parseFloat(document.getElementById("timer").textContent),
        timestamp: new Date().toISOString()
      };
      sessionLogs.push(session);
      saveSessionToServer(session);
    }

    // Reset everything
    resetMix();
    resetTimerDisplay();
    stopTimer();  // Make sure to stop any running timer
    startTimer(); // Start a fresh timer
    
    // Enable color mixing functionality
    enableColorMixing();
    
    document.getElementById("stopBtn").disabled = false;
    document.getElementById("skipBtn").disabled = false;
    document.getElementById("skipBtn").disabled = true;
  });

  document.querySelectorAll(".color-circle").forEach(circle => {
    circle.addEventListener("click", (e) => {
      e.preventDefault();
      const color = circle.dataset.color;
      dropCounts[color]++;
      circle.textContent = dropCounts[color];
      updateCurrentMix();
    });
  });

  document.querySelectorAll(".minus-button").forEach(button => {
    button.addEventListener("click", (e) => {
      e.preventDefault();
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

// Function to save session data
async function saveSessionData() {
  if (!window.currentUserId) return;

  const sessionData = {
    userId: window.currentUserId,
    target: targetColor,
    drops: dropCounts,
    deltaE: parseFloat(document.getElementById('deltaE').textContent),
    time: parseFloat(document.getElementById('timer').textContent),
    timestamp: new Date().toISOString()
  };

  try {
    const response = await fetch('/save_session', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(sessionData)
    });

    if (!response.ok) {
      throw new Error('Failed to save session');
    }

    const result = await response.json();
    if (result.status === 'success') {
      console.log('Session saved successfully');
    }
  } catch (error) {
    console.error('Error saving session:', error);
  }
}

// Handle login form submission
document.addEventListener('DOMContentLoaded', function() {
    const loginForm = document.getElementById('loginForm');
    if (loginForm) {
        loginForm.addEventListener('submit', async function(e) {
            e.preventDefault();
            const userId = document.getElementById('loginId').value.toUpperCase();
            console.log('Attempting login with ID:', userId);
            
            try {
                const response = await fetch('/login', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ userId })
                });
                
                console.log('Login response status:', response.status);
                const data = await response.json();
                console.log('Login response data:', JSON.stringify(data, null, 2));
                
                if (data.status === 'success') {
                    console.log('Login successful, storing user data');
                    localStorage.setItem('userId', userId);
                    localStorage.setItem('userBirthdate', data.birthdate);
                    localStorage.setItem('userGender', data.gender);
                    document.getElementById('userModal').style.display = 'none';
                    resetMix();
                    resetTimerDisplay();
                    // Ensure color mixing is disabled after login until Start is clicked
                    disableColorMixing();
                    console.log('User data stored, modal closed');
                } else {
                    console.log('Login failed:', data.message);
                    alert('Invalid user ID. Please try again.');
                }
            } catch (error) {
                console.error('Login error:', error);
                alert('Invalid user ID. Please try again.');
            }
        });
    } else {
        console.error('Login form not found');
    }
});

// Handle continue button click
document.addEventListener('DOMContentLoaded', function() {
    const continueBtn = document.getElementById('continueBtn');
    if (continueBtn) {
        continueBtn.addEventListener('click', function() {
            document.getElementById('userModal').style.display = 'none';
            resetMix();
            resetTimerDisplay();
            // Ensure color mixing is disabled after continuing until Start is clicked
            disableColorMixing();
        });
    }

    // Handle show login button click
    const showLoginBtn = document.getElementById('showLoginBtn');
    if (showLoginBtn) {
        showLoginBtn.addEventListener('click', function() {
            document.getElementById('registerSection').style.display = 'none';
            document.getElementById('loginSection').style.display = 'block';
        });
    }

    // Handle show register button click
    const showRegisterBtn = document.getElementById('showRegisterBtn');
    if (showRegisterBtn) {
        showRegisterBtn.addEventListener('click', function() {
            document.getElementById('loginSection').style.display = 'none';
            document.getElementById('registerSection').style.display = 'block';
        });
    }
});
