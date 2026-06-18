// main.js (Mixbox JS + Flask colormath backend)

import { startTimer, stopTimer, resetTimerDisplay } from './timer.js';

console.log("✅ main.js loaded");
let sessionLogs = [];
let currentSessionSaved = false; // Flag to prevent duplicate saves

window.lastMixDeltaE = NaN;
window.shadeMatchTargetRgb = [255, 255, 255];

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

// Display user ID if logged in
function displayUserId() {
  const userInfoDiv = document.getElementById('userInfo');
  const userIdDisplay = document.getElementById('userIdDisplay');
  
  if (window.currentUserId && userInfoDiv && userIdDisplay) {
    userIdDisplay.textContent = window.currentUserId;
    userInfoDiv.style.display = 'block';
    console.log('User ID displayed:', window.currentUserId);
  } else {
    console.log('No user ID found in localStorage');
  }
}

// Call displayUserId when the page loads
document.addEventListener('DOMContentLoaded', function() {
  displayUserId();
  
  // Check if user just completed registration (came from Ishihara test)
  const justRegistered = localStorage.getItem('justRegistered');
  if (justRegistered === 'true') {
    // Clear the flag
    localStorage.removeItem('justRegistered');
    // Force display of user ID
    const userId = localStorage.getItem('userId');
    if (userId) {
      window.currentUserId = userId;
      displayUserId();
    }
  }
  
  // Also check for user ID periodically in case it gets set after page load
  // This handles the case where first-time users complete the Ishihara test
  const checkUserIdInterval = setInterval(() => {
    const currentUserId = localStorage.getItem('userId');
    if (currentUserId && currentUserId !== window.currentUserId) {
      window.currentUserId = currentUserId;
      displayUserId();
      clearInterval(checkUserIdInterval); // Stop checking once we find the ID
    }
  }, 1000); // Check every second
  
  // Stop checking after 30 seconds to avoid infinite checking
  setTimeout(() => {
    clearInterval(checkUserIdInterval);
  }, 30000);
});

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
window.shadeMatchDropCounts = dropCounts;

// Define resetMix function
function resetMix() {
  // Reset all drop counts to 0
  document.querySelectorAll('.color-circle').forEach(circle => {
    circle.textContent = '0';
  });
  
  // Reset the mixed color display to white
  document.getElementById('currentMix').style.backgroundColor = 'rgb(255, 255, 255)';
  document.getElementById('mixedRgbValues').textContent = 'RGB: [255, 255, 255]';
  window.lastMixDeltaE = NaN;

  // Reset drop counts object
  dropCounts = {
    white: 0,
    black: 0,
    red: 0,
    yellow: 0,
    blue: 0
  };
  window.shadeMatchDropCounts = dropCounts;

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

// Function to refresh database connection
async function refreshDatabaseConnection() {
  try {
    console.log('🔄 Refreshing database connection...');
    const response = await fetch('/refresh_connection', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    
    if (response.ok) {
      const result = await response.json();
      if (result.status === 'success') {
        console.log('✅ Database connection refreshed');
        return true;
      } else {
        console.warn('⚠️ Connection refresh returned error:', result.message);
        return false;
      }
    } else {
      console.warn('⚠️ Connection refresh failed with status:', response.status);
      return false;
    }
  } catch (error) {
    console.warn('⚠️ Connection refresh failed:', error);
    return false;
  }
}

function saveSessionToServer(session) {
  if (!window.currentUserId) {
    console.error('❌ No user ID found - cannot save session');
    alert('No user ID found. Please log in again.');
    return;
  }

  console.log('💾 Saving session for user ID:', window.currentUserId);
  console.log('Session data:', session);

  // Handle both old format (with target/drops objects) and new format (with individual fields)
  let sessionData;
  if (session.target && session.drops) {
    // Old format - convert to new format
    sessionData = {
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
      timestamp: session.timestamp,
      skipped: session.skipped || false
    };
  } else {
    // New format - use as is
    sessionData = {
      user_id: session.user_id,
      target_r: session.target_r,
      target_g: session.target_g,
      target_b: session.target_b,
      drop_white: session.drop_white,
      drop_black: session.drop_black,
      drop_red: session.drop_red,
      drop_yellow: session.drop_yellow,
      drop_blue: session.drop_blue,
      delta_e: session.delta_e,
      time_sec: session.time_sec,
      timestamp: session.timestamp,
      skipped: session.skipped || false
    };
  }

  console.log('Sending session data to server:', sessionData);

  fetch('/save_session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(sessionData)
  })
  .then(res => {
    console.log('Save session response status:', res.status);
    if (!res.ok) {
      throw new Error(`HTTP error! status: ${res.status}`);
    }
    return res.json();
  })
  .then(data => {
    console.log('Server response:', data);
    if (data.status !== 'success') {
      console.error('Failed to save session:', data.error);
      alert('Failed to save session data. Please try again.');
    } else {
      console.log('Session saved successfully to database');
    }
  })
  .catch(error => {
    console.error('Error saving session:', error);
    alert('Error saving session data. Please check your connection and try again.');
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

  // All available target colors with skin color classifications
  const allTargetColors = [
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

  // Color appearance frequency data from preliminary results (excluding orange, green, purple)
  const colorFrequencyData = {
    // Basic colors (excluding orange, green, purple which are fixed)
    '#FFB3BC': 145, // Pink
    '#FFE4AF': 108, // Peach  
    '#6F7A66': 102, // Custom
    '#71703E': 101, // Olive
    '#547A7A': 96,  // Teal
    '#FF8352': 90,  // Coral
    '#679DAE': 68,  // Turquoise
    '#9DD267': 66,  // Chartreuse
    
    // Skin colors - Light
    '#D1AE90': 48,
    '#BE8870': 39,
    '#AE967E': 22,
    '#C3A28F': 17,
    '#A97367': 16,
    '#CB9781': 11,
    '#E8B7BA': 10,
    '#A58F5E': 18,
    '#B5866A': 20,
    '#DE958F': 1,
    
    // Skin colors - Dark
    '#99856A': 5,
    '#A8856F': 23,
    '#A07E63': 4,
    '#80685C': 3,
    '#584B42': 13,
    '#7B5749': 14,
    '#543B34': 9,
    '#583E2D': 2,
    '#A76662': 21,
    '#A28074': 7,
    '#8F7868': 8,
    '#9F7954': 1,
    '#392D1D': 19,
    '#9D7248': 12,
    '#58482F': 24
  };

  // Function to convert RGB to hex for matching
  function rgbToHex(rgb) {
    return '#' + rgb.map(x => {
      const hex = x.toString(16);
      return hex.length === 1 ? '0' + hex : hex;
    }).join('').toUpperCase();
  }

  // Function to get weighted random selection
  function weightedRandomSelection(items, weights, count) {
    // Calculate total weight
    const totalWeight = weights.reduce((sum, weight) => sum + weight, 0);
    
    // Create cumulative weights
    const cumulativeWeights = [];
    let cumulative = 0;
    for (let i = 0; i < weights.length; i++) {
      cumulative += weights[i];
      cumulativeWeights.push(cumulative);
    }
    
    const selected = [];
    const selectedIndices = new Set();
    
    while (selected.length < count && selected.length < items.length) {
      const random = Math.random() * totalWeight;
      
      for (let i = 0; i < cumulativeWeights.length; i++) {
        if (random <= cumulativeWeights[i] && !selectedIndices.has(i)) {
          selected.push(items[i]);
          selectedIndices.add(i);
          break;
        }
      }
    }
    
    return selected;
  }

  // Function to generate randomized color selection for a session
  function generateRandomizedColors() {
    // Always include first 3 basic colors (Orange, Purple, Green)
    const firstThreeBasic = allTargetColors.slice(0, 3);
    
    // Get remaining basic colors (indices 3-10, which are 8 colors)
    const remainingBasic = allTargetColors.slice(3, 11);
    
    // Calculate weights for remaining basic colors (inverse of frequency)
    const basicWeights = remainingBasic.map(color => {
      const hex = rgbToHex(color.rgb);
      const frequency = colorFrequencyData[hex] || 1; // Default to 1 if not found
      return 1 / frequency; // Inverse weight - lower frequency = higher weight
    });
    
    // Weighted selection of 3 from remaining basic colors
    const selectedRemainingBasic = weightedRandomSelection(remainingBasic, basicWeights, 3);
    
    // Get all skin colors (indices 11-39, which are 29 colors)
    const skinColors = allTargetColors.slice(11);
    
    // Calculate weights for skin colors (inverse of frequency)
    const skinWeights = skinColors.map(color => {
      const hex = rgbToHex(color.rgb);
      const frequency = colorFrequencyData[hex] || 1; // Default to 1 if not found
      return 1 / frequency; // Inverse weight - lower frequency = higher weight
    });
    
    // Weighted selection of 5 skin colors
    const selectedSkinColors = weightedRandomSelection(skinColors, skinWeights, 5);
    
    // Combine all selected colors
    const selectedColors = [
      ...firstThreeBasic,
      ...selectedRemainingBasic,
      ...selectedSkinColors
    ];
    
    console.log('🎨 Generated weighted randomized color selection:');
    console.log('- Fixed 3 basic colors:', firstThreeBasic.map(c => c.name));
    console.log('- 3 weighted remaining basic colors:', selectedRemainingBasic.map(c => c.name));
    console.log('- 5 weighted skin colors:', selectedSkinColors.map(c => c.name));
    console.log('- Total colors for this session:', selectedColors.length);
    
    return selectedColors;
  }

  // Generate the randomized target colors for this session
  const targetColors = generateRandomizedColors();

  let currentTargetIndex = 0;
  let currentTargetColor = targetColors[0];
  let targetColor = currentTargetColor.rgb;

  function setGameTargetRgb(rgb) {
    targetColor = rgb;
    window.shadeMatchTargetRgb = rgb;
  }
  setGameTargetRgb(currentTargetColor.rgb);

  function showSkipPerceptionModal() {
    return new Promise((resolve) => {
      const modal = document.getElementById('skipPerceptionModal');
      if (!modal) {
        resolve(null);
        return;
      }
      modal.style.display = 'flex';
      const options = [
        { id: 'skipPerceptionIdentical', value: 'identical' },
        { id: 'skipPerceptionAcceptable', value: 'acceptable' },
        { id: 'skipPerceptionUnacceptable', value: 'unacceptable' }
      ];
      const handlers = [];
      const finish = (value) => {
        modal.style.display = 'none';
        for (const { el, fn } of handlers) {
          el.removeEventListener('click', fn);
        }
        resolve(value);
      };
      for (const { id, value } of options) {
        const el = document.getElementById(id);
        if (!el) continue;
        const fn = () => finish(value);
        el.addEventListener('click', fn);
        handlers.push({ el, fn });
      }
    });
  }

  function updateCurrentMix() {
    const totalDrops = Object.values(dropCounts).reduce((a, b) => a + b, 0);
    if (totalDrops === 0) {
      updateBox("currentMix", [255, 255, 255]);
      window.lastMixDeltaE = NaN;
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
      window.lastMixDeltaE = data.delta_e;

      console.log(`Current DeltaE: ${data.delta_e}, Threshold check: ${data.delta_e <= 0.01}`);

      if (data.delta_e <= 0.01) { // Use threshold instead of exact equality for floating point
        console.log('🎯 Perfect match achieved! Auto-saving session...');
        stopTimer();
        const session = {
          user_id: window.currentUserId,
          target: targetColor,
          drops: { ...dropCounts },
          deltaE: data.delta_e,
          time: parseFloat(document.getElementById("timer").textContent),
          timestamp: new Date().toISOString(),
          skipped: false  // Explicitly mark as completed (not skipped)
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
  document.getElementById("startBtn").addEventListener("click", async () => {
    // Refresh database connection before starting
    await refreshDatabaseConnection();
    
    // Generate new randomized colors for the start
    const newTargetColors = generateRandomizedColors();
    targetColors.length = 0; // Clear existing array
    targetColors.push(...newTargetColors); // Add new colors
    
    currentTargetIndex = 0;
    currentTargetColor = targetColors[currentTargetIndex];
    setGameTargetRgb(currentTargetColor.rgb);
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
    const currentDeltaE = Number.isFinite(window.lastMixDeltaE) ? window.lastMixDeltaE : NaN;
    
    // Always save the current session data when Stop is clicked
    if (!isNaN(currentDeltaE)) {  // Only save if we have a valid DeltaE
      console.log('🛑 Stop button clicked - saving session as SKIPPED with DeltaE:', currentDeltaE);
      const session = {
        user_id: window.currentUserId,
        target: targetColor,
        drops: { ...dropCounts },
        deltaE: currentDeltaE,
        time: parseFloat(document.getElementById("timer").textContent),
        timestamp: new Date().toISOString(),
        skipped: true  // Mark as skipped since user manually stopped
      };
      sessionLogs.push(session);
      
      // Save to server with correct data structure
      const sessionData = {
        user_id: window.currentUserId,
        target_r: targetColor[0],
        target_g: targetColor[1],
        target_b: targetColor[2],
        drop_white: dropCounts.white,
        drop_black: dropCounts.black,
        drop_red: dropCounts.red,
        drop_yellow: dropCounts.yellow,
        drop_blue: dropCounts.blue,
        delta_e: currentDeltaE,
        time_sec: parseFloat(document.getElementById("timer").textContent),
        timestamp: new Date().toISOString(),
        skipped: true
      };
      saveSessionToServer(sessionData);
    } else {
      console.log('🛑 Stop button clicked but no valid DeltaE to save');
    }
    
    // Disable color mixing functionality
    disableColorMixing();
    
    document.getElementById("skipBtn").disabled = false;
    document.getElementById("stopBtn").disabled = true;
  });

  document.getElementById("skipBtn").addEventListener("click", async () => {
    // Refresh database connection before skipping
    await refreshDatabaseConnection();

    const currentDeltaE = Number.isFinite(window.lastMixDeltaE) ? window.lastMixDeltaE : NaN;

    // Check if we're in a "Stop then Skip" scenario by checking if color mixing is disabled
    const isAfterStop = document.getElementById("palette").style.display === "none";

    // If we already auto-saved a perfect match (ΔE≤0.01), this round is complete even if the user
    // added more drops and raised ΔE before pressing "Next color" — do not treat as mid-trial skip.
    const alreadyCompletedThisColor = window.currentSessionSaved === true;

    const shouldSaveSkip =
      currentDeltaE > 0.01 && !isAfterStop && !alreadyCompletedThisColor;
    console.log(
      `⏭️ Skip button clicked - DeltaE: ${currentDeltaE}, After Stop: ${isAfterStop}, alreadyCompleted: ${alreadyCompletedThisColor}, Will save skip: ${shouldSaveSkip}`
    );

    if (shouldSaveSkip) {
      const skipPerception = await showSkipPerceptionModal();
      if (!skipPerception) {
        console.warn('Skip perception modal closed without selection');
        return;
      }
      const skipData = {
        user_id: window.currentUserId,
        target_r: targetColor[0],
        target_g: targetColor[1],
        target_b: targetColor[2],
        drop_white: dropCounts.white || 0,
        drop_black: dropCounts.black || 0,
        drop_red: dropCounts.red || 0,
        drop_yellow: dropCounts.yellow || 0,
        drop_blue: dropCounts.blue || 0,
        time_sec: parseFloat(document.getElementById("timer").textContent),
        timestamp: new Date().toISOString(),
        delta_e: currentDeltaE,
        skip_perception: skipPerception
      };
      try {
        const res = await fetch('/save_skip', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(skipData)
        });
        const data = await res.json();
        console.log('Save skip response status:', res.status, data);
        if (!res.ok || data.status !== 'success') {
          console.error('Failed to save skip:', data.error);
          alert('Failed to save skip data. Please try again.');
          return;
        }
        console.log('Skip saved successfully to database');
      } catch (error) {
        console.error('Error saving skip:', error);
        alert('Error saving skip data. Please check your connection and try again.');
        return;
      }
    } else {
      console.log(
        'Skip not saved - already completed this color, ΔE≤threshold completion, or saved by Stop'
      );
    }

    currentTargetIndex++;
    if (currentTargetIndex < targetColors.length) {
      currentTargetColor = targetColors[currentTargetIndex];
      setGameTargetRgb(currentTargetColor.rgb);
      updateBox("targetColor", targetColor);
      resetMix();
      
      // Stop current timer and reset display before starting new timer
      stopTimer();
      resetTimerDisplay();
      startTimer();
      
      // Enable color mixing functionality for the new color
      enableColorMixing();
      
      document.getElementById("skipBtn").disabled = false;
      document.getElementById("stopBtn").disabled = false;
      document.getElementById("skipBtn").textContent = "Skip";
    } else {
      // All colors completed - show congratulatory message with confetti and redirect
      const congratulations = `
        <div id="congratulations-modal" style="
          position: fixed;
          top: 0;
          left: 0;
          width: 100%;
          height: 100%;
          background: rgba(0, 0, 0, 0.8);
          display: flex;
          justify-content: center;
          align-items: center;
          z-index: 10000;
          font-family: sans-serif;
        ">
          <div style="
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 40px;
            border-radius: 20px;
            text-align: center;
            max-width: 500px;
            margin: 20px;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.3);
            position: relative;
            overflow: hidden;
          ">
            <div style="font-size: 4em; margin-bottom: 20px;">🎉</div>
            <h2 style="margin: 0 0 20px 0; font-size: 2.5em; font-weight: 300;">Congratulations!</h2>
            <p style="margin: 0 0 30px 0; font-size: 1.2em; line-height: 1.6;">
              You have successfully completed all color matching challenges!<br>
              Your dedication and color perception skills are impressive.
            </p>
            <p style="margin: 0 0 30px 0; font-size: 1em; opacity: 0.9;">
              You will now be redirected to view your detailed results and performance analysis.
            </p>
            <div style="
              display: inline-block;
              background: rgba(255, 255, 255, 0.2);
              padding: 15px 30px;
              border-radius: 25px;
              font-size: 1.1em;
              font-weight: 500;
            ">
              Redirecting to results...
            </div>
          </div>
        </div>
      `;
      
      document.body.insertAdjacentHTML('beforeend', congratulations);
      
      // Create confetti effect
      createConfetti();
      
      // Disable all buttons
      document.getElementById("skipBtn").disabled = true;
      document.getElementById("stopBtn").disabled = true;
      
      // Redirect to results page after 4 seconds (giving time for confetti)
      setTimeout(() => {
        window.location.href = '/results';
      }, 4000);
    }
  });

  document.getElementById("restartBtn").addEventListener("click", async () => {
    // Refresh database connection before restarting
    await refreshDatabaseConnection();
    
    // Generate new randomized colors for the restart
    const newTargetColors = generateRandomizedColors();
    targetColors.length = 0; // Clear existing array
    targetColors.push(...newTargetColors); // Add new colors
    
    currentTargetIndex = 0;
    currentTargetColor = targetColors[currentTargetIndex];
    setGameTargetRgb(currentTargetColor.rgb);
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
    const currentDeltaE = Number.isFinite(window.lastMixDeltaE) ? window.lastMixDeltaE : NaN;
    if (!isNaN(currentDeltaE)) {  // Only store if we have a valid DeltaE
      console.log('🔄 Retry button clicked - saving session with DeltaE:', currentDeltaE);
      const session = {
        user_id: window.currentUserId,
        target: targetColor,
        drops: { ...dropCounts },
        deltaE: currentDeltaE,
        time: parseFloat(document.getElementById("timer").textContent),
        timestamp: new Date().toISOString(),
        skipped: true  // Mark as skipped since user chose to retry
      };
      sessionLogs.push(session);
      saveSessionToServer(session);
    } else {
      console.log('🔄 Retry button clicked but no valid DeltaE to save');
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
    target: window.shadeMatchTargetRgb,
    drops: window.shadeMatchDropCounts,
    deltaE: Number.isFinite(window.lastMixDeltaE) ? window.lastMixDeltaE : NaN,
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
                    window.currentUserId = userId; // Update global variable
                    document.getElementById('userModal').style.display = 'none';
                    resetMix();
                    resetTimerDisplay();
                    // Ensure color mixing is disabled after login until Start is clicked
                    disableColorMixing();
                    // Display user ID after successful login
                    displayUserId();
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

// Confetti animation function
function createConfetti() {
  const colors = ['#ff6b6b', '#4ecdc4', '#45b7d1', '#96ceb4', '#feca57', '#ff9ff3', '#54a0ff', '#5f27cd'];
  const confettiCount = 150;
  
  for (let i = 0; i < confettiCount; i++) {
    setTimeout(() => {
      createConfettiPiece(colors);
    }, i * 20); // Stagger the creation for a more natural effect
  }
}

function createConfettiPiece(colors) {
  const confetti = document.createElement('div');
  const color = colors[Math.floor(Math.random() * colors.length)];
  const size = Math.random() * 8 + 4; // Random size between 4-12px
  const startX = Math.random() * window.innerWidth;
  const startY = -10;
  const endY = window.innerHeight + 10;
  const rotation = Math.random() * 360;
  const rotationSpeed = (Math.random() - 0.5) * 20;
  const horizontalDrift = (Math.random() - 0.5) * 100;
  
  confetti.style.cssText = `
    position: fixed;
    left: ${startX}px;
    top: ${startY}px;
    width: ${size}px;
    height: ${size}px;
    background: ${color};
    border-radius: ${Math.random() > 0.5 ? '50%' : '0'};
    pointer-events: none;
    z-index: 10001;
    box-shadow: 0 0 6px ${color};
  `;
  
  document.body.appendChild(confetti);
  
  // Animate the confetti
  let startTime = null;
  const duration = 3000 + Math.random() * 2000; // 3-5 seconds
  
  function animate(currentTime) {
    if (!startTime) startTime = currentTime;
    const elapsed = currentTime - startTime;
    const progress = Math.min(elapsed / duration, 1);
    
    // Easing function for natural fall
    const easeOut = 1 - Math.pow(1 - progress, 3);
    
    const currentY = startY + (endY - startY) * easeOut;
    const currentX = startX + horizontalDrift * Math.sin(progress * Math.PI);
    const currentRotation = rotation + rotationSpeed * elapsed / 1000;
    
    confetti.style.transform = `translate(${currentX - startX}px, ${currentY - startY}px) rotate(${currentRotation}deg)`;
    confetti.style.opacity = 1 - progress;
    
    if (progress < 1) {
      requestAnimationFrame(animate);
    } else {
      confetti.remove();
    }
  }
  
  requestAnimationFrame(animate);
}
