// JavaScript syntax and concepts explained in Python terms:
// - 'class' is similar to Python classes, used to define a blueprint for objects
// - 'constructor()' is like Python's __init__ method, called when creating a new object
// - 'this' is like Python's 'self', refers to the current object instance
// - 'const' and 'let' are like Python variables, but 'const' cannot be reassigned
// - '=>' is an arrow function, similar to Python lambda functions
// - '{}' creates an object (like Python dictionaries)
// - '[]' creates an array (like Python lists)
// - 'document.querySelector()' is like Python's DOM manipulation
// - 'addEventListener()' is like Python's event handling
// - 'console.log()' is like Python's print()
// - 'Math' is like Python's math module
// - 'Plotly' is a JavaScript library for plotting (similar to matplotlib in Python)

// Spectral color mixing implementation
class SpectralMixer {
    constructor() {
        // Dynamically determine wavelengths and sample size from pigment data
        const firstColor = Object.keys(spectrum_plots)[0];
        this.wavelengths = spectrum_plots[firstColor].wavelengths;
        this.SIZE = this.wavelengths.length;
        this.GAMMA = 2.4;
        this.dropCounts = { red: 0, yellow: 0, blue: 0 };
        this.initializeControls();
        this.initializePlots();
    }

    // Kubelka-Munk functions from spectral_by_wijnen.js
    KS(R) {
        return (1 - R) ** 2 / (2 * R);
    }

    KM(KS) {
        return 1 + KS - (KS ** 2 + 2 * KS) ** 0.5;
    }

    // Color space conversion functions
    uncompand(x) {
        return x > 0.04045 ? ((x + 0.055) / 1.055) ** this.GAMMA : x / 12.92;
    }

    compand(x) {
        return x > 0.0031308 ? 1.055 * x ** (1.0 / this.GAMMA) - 0.055 : x * 12.92;
    }

    sRGB_to_lRGB(sRGB) {
        return sRGB.map(x => this.uncompand(x / 255));
    }

    lRGB_to_sRGB(lRGB) {
        return lRGB.map(x => Math.round(this.compand(x) * 255));
    }

    // Matrix multiplication utility
    mulMatVec(m, v) {
        return m.map(row => row.reduce((acc, val, i) => acc + val * v[i], 0));
    }

    // Color space conversion matrices from spectral_by_wijnen.js
    CONVERSION = {
        RGB_XYZ: [
            [0.41239079926595934, 0.357584339383878, 0.1804807884018343],
            [0.21263900587151027, 0.715168678767756, 0.07219231536073371],
            [0.01933081871559182, 0.11919477979462598, 0.9505321522496607]
        ],
        XYZ_RGB: [
            [3.2409699419045226, -1.537383177570094, -0.4986107602930034],
            [-0.9692436362808796, 1.8759675015077202, 0.04155505740717559],
            [0.05563007969699366, -0.20397695888897652, 1.0569715142428786]
        ]
    };

    // CIE Color Matching Functions from spectral_by_wijnen.js
    CIE = {
        CMF: [
            [0.0000646919989576, 0.0002194098998132, 0.0011205743509343, /* ... rest of the values ... */],
            [0.000001844289444, 0.0000062053235865, 0.0000310096046799, /* ... rest of the values ... */],
            [0.000305017147638, 0.0010368066663574, 0.0053131363323992, /* ... rest of the values ... */]
        ]
    };

    initializeControls() {
        console.log('Initializing controls');
        
        // Only initialize controls for available pigments
        Object.keys(this.dropCounts).forEach(color => {
            const control = document.querySelector(`.color-control[data-color="${color}"]`);
            if (!control) {
                console.error(`Could not find control for ${color}`);
                return;
            }

            const buttons = {
                plus: control.querySelector('.drop-button:last-child'),
                minus: control.querySelector('.drop-button:first-child'),
                circle: control.querySelector('.color-circle')
            };

            if (buttons.plus && buttons.minus && buttons.circle) {
                buttons.plus.addEventListener('click', () => {
                    this.dropCounts[color]++;
                    buttons.circle.textContent = this.dropCounts[color];
                    this.updateMixedColor();
                });

                buttons.minus.addEventListener('click', () => {
                    if (this.dropCounts[color] > 0) {
                        this.dropCounts[color]--;
                        buttons.circle.textContent = this.dropCounts[color];
                        this.updateMixedColor();
                    }
                });

                // Set initial color from spectrum_plots
                const plotData = spectrum_plots[color];
                if (plotData) {
                    const [r, g, b] = plotData.rgb;
                    buttons.circle.style.backgroundColor = `rgb(${r}, ${g}, ${b})`;
                    buttons.circle.style.color = this.getContrastColor(r, g, b);
                    buttons.circle.textContent = '0';
                }
            }
        });

        // Initialize mixed color circle
        this.mixedCircle = document.querySelector('.mixed-color .color-circle');
        if (this.mixedCircle) {
            this.updateMixedColor();
        }
    }

    initializePlots() {
        // Initialize individual color plots
        Object.entries(spectrum_plots).forEach(([color, data]) => {
            const plotDiv = document.getElementById(`${color}Spectrum`);
            if (!plotDiv) return;

            const [r, g, b] = data.rgb;
            console.log(`${color} RGB:`, r, g, b);

            const trace = {
                x: data.wavelengths,
                y: data.reflectances,
                type: 'scatter',
                mode: 'lines',
                name: color.charAt(0).toUpperCase() + color.slice(1),
                line: {
                    color: `rgb(${r}, ${g}, ${b})`,
                    width: 2
                }
            };

            const layout = {
                title: `${color.charAt(0).toUpperCase() + color.slice(1)} Spectrum`,
                xaxis: {
                    title: 'Wavelength (nm)',
                    range: [400, 700],
                    showgrid: true,
                    gridcolor: '#ddd'
                },
                yaxis: {
                    title: 'Reflectance',
                    range: [0, 1],
                    showgrid: true,
                    gridcolor: '#ddd'
                },
                margin: { t: 30, r: 20, b: 40, l: 50 },
                paper_bgcolor: 'white',
                plot_bgcolor: 'white'
            };

            Plotly.newPlot(plotDiv, [trace], layout);
        });

        // Initialize mixed spectrum plot
        const mixedPlotDiv = document.getElementById('mixedSpectrum');
        if (mixedPlotDiv) {
            this.updateMixedColor();
        }
    }

    updateMixedColor() {
        console.log('Updating mixed color...');
        console.log('Drop counts:', this.dropCounts);

        // Calculate total drops
        const totalDrops = Object.values(this.dropCounts).reduce((a, b) => a + b, 0);
        if (totalDrops === 0) {
            if (this.mixedCircle) {
                this.mixedCircle.style.backgroundColor = 'rgb(255, 255, 255)';
                this.mixedCircle.style.color = '#000000';
            }
            // Plot white spectrum
            const mixedPlotDiv = document.getElementById('mixedSpectrum');
            if (mixedPlotDiv) {
                Plotly.newPlot(mixedPlotDiv, [{
                    x: this.wavelengths,
                    y: Array(this.SIZE).fill(1),
                    type: 'scatter',
                    mode: 'lines',
                    name: 'Mixed',
                    line: { color: 'rgb(255,255,255)', width: 2 }
                }], {
                    title: 'Mixed Spectrum',
                    xaxis: { title: 'Wavelength (nm)', range: [400, 700], showgrid: true, gridcolor: '#ddd' },
                    yaxis: { title: 'Reflectance', range: [0, 1], showgrid: true, gridcolor: '#ddd' },
                    margin: { t: 30, r: 20, b: 40, l: 50 },
                    paper_bgcolor: 'white', plot_bgcolor: 'white'
                });
            }
            return;
        }

        // Wijnen's Kubelka-Munk mixing
        let mixedKS = Array(this.SIZE).fill(0);
        let totalWeight = 0;
        // 1. Compute weighted sum of K/S for each pigment
        Object.entries(this.dropCounts).forEach(([color, drops]) => {
            if (drops > 0) {
                const data = spectrum_plots[color];
                if (data) {
                    totalWeight += drops;
                }
            }
        });
        if (totalWeight === 0) totalWeight = 1; // avoid division by zero
        Object.entries(this.dropCounts).forEach(([color, drops]) => {
            if (drops > 0) {
                const data = spectrum_plots[color];
                if (data) {
                    const weight = drops / totalWeight;
                    for (let i = 0; i < this.SIZE; i++) {
                        const R = Math.max(0.0001, Math.min(0.9999, data.reflectances[i])); // avoid div by zero
                        mixedKS[i] += this.KS(R) * weight;
                    }
                }
            }
        });
        // 2. Convert mixed K/S back to reflectance using Kubelka-Munk inversion
        let mixedSpectrum = mixedKS.map(KS => {
            const sqrt = Math.sqrt(KS * KS + 2 * KS);
            return Math.max(0, Math.min(1, 1 + KS - sqrt));
        });
        // 3. Convert mixed spectrum to RGB
        const [r, g, b] = this.spectrumToRGB(mixedSpectrum);
        // Update mixed color circle
        if (this.mixedCircle) {
            this.mixedCircle.style.backgroundColor = `rgb(${r}, ${g}, ${b})`;
            this.mixedCircle.style.color = this.getContrastColor(r, g, b);
        }
        // Update mixed spectrum plot
        const mixedPlotDiv = document.getElementById('mixedSpectrum');
        if (mixedPlotDiv) {
            const trace = {
                x: this.wavelengths,
                y: mixedSpectrum,
                type: 'scatter',
                mode: 'lines',
                name: 'Mixed',
                line: {
                    color: `rgb(${r}, ${g}, ${b})`,
                    width: 2
                }
            };
            const layout = {
                title: 'Mixed Spectrum',
                xaxis: {
                    title: 'Wavelength (nm)',
                    range: [400, 700],
                    showgrid: true,
                    gridcolor: '#ddd'
                },
                yaxis: {
                    title: 'Reflectance',
                    range: [0, 1],
                    showgrid: true,
                    gridcolor: '#ddd'
                },
                margin: { t: 30, r: 20, b: 40, l: 50 },
                paper_bgcolor: 'white',
                plot_bgcolor: 'white'
            };
            Plotly.newPlot(mixedPlotDiv, [trace], layout);
        }
    }

    // Linear interpolation utility
    interpolateArray(x, y, xq) {
        // x: original x values (wavelengths), y: original y values (reflectances), xq: query x values
        let yq = [];
        for (let i = 0; i < xq.length; i++) {
            const xi = xq[i];
            if (xi <= x[0]) {
                yq.push(y[0]);
            } else if (xi >= x[x.length - 1]) {
                yq.push(y[y.length - 1]);
            } else {
                let j = 1;
                while (x[j] < xi) j++;
                const x0 = x[j - 1], x1 = x[j];
                const y0 = y[j - 1], y1 = y[j];
                yq.push(y0 + (y1 - y0) * (xi - x0) / (x1 - x0));
            }
        }
        return yq;
    }

    spectrumToRGB(spectrum) {
        // CIE 1931 color matching functions (31 points, 400–700 nm, 10 nm steps)
        const x_bar = [0.0143,0.0435,0.1344,0.2839,0.3483,0.3362,0.2908,0.1954,0.0956,0.0320,0.0049,0.0093,0.0633,0.1655,0.2904,0.4334,0.5945,0.7621,0.9163,1.0263,1.0622,1.0026,0.8544,0.6424,0.4479,0.2835,0.1649,0.0874,0.0468,0.0227,0.0114];
        const y_bar = [0.0004,0.0012,0.0040,0.0116,0.023,0.038,0.060,0.091,0.139,0.208,0.323,0.503,0.710,0.862,0.954,0.995,0.995,0.952,0.870,0.757,0.631,0.503,0.381,0.265,0.175,0.107,0.061,0.032,0.017,0.0082,0.0041];
        const z_bar = [0.0679,0.2074,0.6456,1.3856,1.7471,1.7721,1.6692,1.2876,0.8130,0.4652,0.2720,0.1582,0.0782,0.0422,0.0203,0.0087,0.0039,0.0021,0.0017,0.0011,0.0008,0.0003,0.0002,0.0000,0.0000,0.0000,0.0000,0.0000,0.0000,0.0000,0.0000];
        const cmf_wavelengths = Array.from({length: 31}, (_, i) => 400 + i * 10);
        // Resample spectrum to match CMF wavelengths
        const resampled = this.interpolateArray(this.wavelengths, spectrum, cmf_wavelengths);
        // Calculate XYZ values
        let X = 0, Y = 0, Z = 0;
        for (let i = 0; i < 31; i++) {
            X += resampled[i] * x_bar[i];
            Y += resampled[i] * y_bar[i];
            Z += resampled[i] * z_bar[i];
        }
        // Normalize
        const sum = X + Y + Z;
        if (sum > 0) {
            X /= sum;
            Y /= sum;
            Z /= sum;
        }
        // Convert XYZ to linear RGB
        const lRGB = this.mulMatVec(this.CONVERSION.XYZ_RGB, [X, Y, Z]);
        // Convert linear RGB to sRGB with gamma correction
        return this.lRGB_to_sRGB(lRGB);
    }

    getContrastColor(r, g, b) {
        // Calculate relative luminance
        const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
        return luminance > 0.5 ? '#000000' : '#ffffff';
    }
}

// Initialize the spectral mixer when the page loads
document.addEventListener('DOMContentLoaded', function() {
    console.log('Initializing spectral mixer...');
    console.log('Spectrum plots:', spectrum_plots);
    
    // Initialize plots for each available color
    Object.keys(spectrum_plots).forEach(color => {
        console.log(`Initializing ${color}...`);
        const plotDiv = document.getElementById(`${color}Spectrum`);
        if (!plotDiv) {
            console.error(`Could not find plot div for ${color}`);
            return;
        }

        const plotData = spectrum_plots[color];
        if (!plotData) {
            console.error(`No data found for ${color}`);
            return;
        }

        const [r, g, b] = plotData.rgb;
        console.log(`${color} RGB:`, r, g, b);

        const trace = {
            x: plotData.wavelengths,
            y: plotData.reflectances,
            type: 'scatter',
            mode: 'lines',
            name: color.charAt(0).toUpperCase() + color.slice(1),
            line: {
                color: `rgb(${r}, ${g}, ${b})`,
                width: 2
            }
        };

        const layout = {
            title: `${color.charAt(0).toUpperCase() + color.slice(1)} Spectrum`,
            xaxis: {
                title: 'Wavelength (nm)',
                range: [400, 700],
                showgrid: true,
                gridcolor: '#ddd'
            },
            yaxis: {
                title: 'Reflectance',
                range: [0, 1],
                showgrid: true,
                gridcolor: '#ddd'
            },
            margin: { t: 30, r: 20, b: 40, l: 50 },
            paper_bgcolor: 'white',
            plot_bgcolor: 'white'
        };

        Plotly.newPlot(plotDiv, [trace], layout);
    });

    // Initialize the spectral mixer
    window.spectralMixer = new SpectralMixer();
}); 