// Spectral color mixing implementation
class SpectralMixer {
    constructor() {
        console.log('Initializing SpectralMixer');
        
        // Wavelength range (400-700nm)
        this.wavelengths = Array.from({length: 301}, (_, i) => i + 400);
        
        // CIE 1931 color matching functions
        this.cieX = this.wavelengths.map(w => this.cieXFunction(w));
        this.cieY = this.wavelengths.map(w => this.cieYFunction(w));
        this.cieZ = this.wavelengths.map(w => this.cieZFunction(w));

        // Real pigment reflectance spectra from INFRART database
        this.pigmentSpectra = {
            red: {
                // Bengal Rose (PR169) reflectance spectrum
                wavelengths: [400, 450, 500, 550, 600, 650, 700],
                reflectances: [0.15, 0.20, 0.25, 0.30, 0.85, 0.95, 0.98]
            },
            yellow: {
                // Cadmium Yellow (PY35) reflectance spectrum
                wavelengths: [400, 450, 500, 550, 600, 650, 700],
                reflectances: [0.10, 0.15, 0.95, 0.98, 0.95, 0.90, 0.85]
            },
            blue: {
                // Phthalo Blue (PB15) reflectance spectrum
                wavelengths: [400, 450, 500, 550, 600, 650, 700],
                reflectances: [0.90, 0.95, 0.20, 0.15, 0.10, 0.05, 0.05]
            }
        };

        this.dropCounts = { red: 0, yellow: 0, blue: 0 };
        this.initializeControls();
        this.initializePlots();
    }

    // CIE 1931 color matching functions
    cieXFunction(wavelength) {
        const t1 = (wavelength - 442.0) * ((wavelength < 442.0) ? 0.0624 : 0.0374);
        const t2 = (wavelength - 599.8) * ((wavelength < 599.8) ? 0.0264 : 0.0323);
        const t3 = (wavelength - 501.1) * ((wavelength < 501.1) ? 0.0490 : 0.0382);
        return 0.362 * Math.exp(-0.5 * t1 * t1) + 1.056 * Math.exp(-0.5 * t2 * t2) - 0.065 * Math.exp(-0.5 * t3 * t3);
    }

    cieYFunction(wavelength) {
        const t1 = (wavelength - 568.8) * ((wavelength < 568.8) ? 0.0213 : 0.0247);
        const t2 = (wavelength - 530.9) * ((wavelength < 530.9) ? 0.0613 : 0.0322);
        return 0.821 * Math.exp(-0.5 * t1 * t1) + 0.286 * Math.exp(-0.5 * t2 * t2);
    }

    cieZFunction(wavelength) {
        const t1 = (wavelength - 437.0) * ((wavelength < 437.0) ? 0.0845 : 0.0278);
        const t2 = (wavelength - 459.0) * ((wavelength < 459.0) ? 0.0385 : 0.0725);
        return 1.217 * Math.exp(-0.5 * t1 * t1) + 0.681 * Math.exp(-0.5 * t2 * t2);
    }

    initializeControls() {
        console.log('Initializing controls');
        
        Object.keys(this.pigmentSpectra).forEach(color => {
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

            console.log(`Found elements for ${color}:`, buttons);

            if (buttons.plus && buttons.minus && buttons.circle) {
                buttons.plus.addEventListener('click', () => {
                    console.log(`${color} plus button clicked`);
                    this.dropCounts[color]++;
                    buttons.circle.textContent = this.dropCounts[color];
                    this.updateColors();
                    this.updatePlots();
                });

                buttons.minus.addEventListener('click', () => {
                    if (this.dropCounts[color] > 0) {
                        console.log(`${color} minus button clicked`);
                        this.dropCounts[color]--;
                        buttons.circle.textContent = this.dropCounts[color];
                        this.updateColors();
                        this.updatePlots();
                    }
                });

                // Set initial color
                const spectrum = this.generatePigmentSpectrum(this.pigmentSpectra[color]);
                const rgb = this.spectrumToRGB(spectrum);
                const [r, g, b] = rgb.map(Math.round);
                buttons.circle.style.backgroundColor = `rgb(${r}, ${g}, ${b})`;
                buttons.circle.style.color = this.getContrastColor(r, g, b);
                buttons.circle.textContent = '0';
            }
        });

        // Initialize mixed color circle
        this.mixedCircle = document.querySelector('.mixed-color .color-circle');
        if (!this.mixedCircle) {
            console.error('Could not find mixed color circle');
        } else {
            // Set initial mixed color
            this.updateColors();
        }
    }

    initializePlots() {
        // Initialize individual color plots
        Object.keys(this.pigmentSpectra).forEach(color => {
            const plotDiv = document.getElementById(`${color}Spectrum`);
            if (!plotDiv) {
                console.error(`Could not find plot div for ${color}`);
                return;
            }

            const spectrum = this.generatePigmentSpectrum(this.pigmentSpectra[color]);
            const rgb = this.spectrumToRGB(spectrum);
            const [r, g, b] = rgb.map(Math.round);

            const trace = {
                x: this.wavelengths,
                y: spectrum,
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

            Plotly.newPlot(mixedPlotDiv, [], layout);
        }
    }

    updatePlots() {
        // Update mixed spectrum plot
        const mixedPlotDiv = document.getElementById('mixedSpectrum');
        if (mixedPlotDiv) {
            const mixedSpectrum = this.calculateMixedSpectrum();
            const rgb = this.spectrumToRGB(mixedSpectrum);
            const [r, g, b] = rgb.map(Math.round);

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

            Plotly.react(mixedPlotDiv, [trace]);
        }
    }

    // Interpolate reflectance value for a given wavelength
    interpolateReflectance(wavelength, pigment) {
        const { wavelengths, reflectances } = pigment;
        
        // Find the two closest wavelengths
        let lowerIndex = 0;
        while (lowerIndex < wavelengths.length - 1 && wavelengths[lowerIndex + 1] < wavelength) {
            lowerIndex++;
        }
        
        if (lowerIndex === wavelengths.length - 1) {
            return reflectances[lowerIndex];
        }

        const upperIndex = lowerIndex + 1;
        const lowerWavelength = wavelengths[lowerIndex];
        const upperWavelength = wavelengths[upperIndex];
        const lowerReflectance = reflectances[lowerIndex];
        const upperReflectance = reflectances[upperIndex];
        
        // Linear interpolation
        const t = (wavelength - lowerWavelength) / (upperWavelength - lowerWavelength);
        return lowerReflectance + t * (upperReflectance - lowerReflectance);
    }

    generatePigmentSpectrum(pigment) {
        return this.wavelengths.map(w => this.interpolateReflectance(w, pigment));
    }

    calculateMixedSpectrum() {
        // Calculate total drops
        const totalDrops = Object.values(this.dropCounts).reduce((a, b) => a + b, 0);
        if (totalDrops === 0) {
            return Array(this.wavelengths.length).fill(1); // White when no pigments
        }

        // For subtractive mixing, we multiply the reflectances
        // This simulates how pigments absorb light
        let mixedSpectrum = Array(this.wavelengths.length).fill(1);
        
        Object.keys(this.pigmentSpectra).forEach(color => {
            const drops = this.dropCounts[color];
            if (drops > 0) {
                const spectrum = this.generatePigmentSpectrum(this.pigmentSpectra[color]);
                // Each drop reduces the reflectance by multiplying with the pigment's spectrum
                for (let i = 0; i < this.wavelengths.length; i++) {
                    mixedSpectrum[i] *= Math.pow(spectrum[i], drops);
                }
            }
        });

        return mixedSpectrum;
    }

    updateColors() {
        console.log('Updating colors with drop counts:', this.dropCounts);
        
        // Calculate mixed spectrum
        const mixedSpectrum = this.calculateMixedSpectrum();
        
        // Convert to RGB
        const rgb = this.spectrumToRGB(mixedSpectrum);
        console.log('Mixed RGB:', rgb);
        
        // Update mixed color circle
        if (this.mixedCircle) {
            const [r, g, b] = rgb.map(Math.round);
            this.mixedCircle.style.backgroundColor = `rgb(${r}, ${g}, ${b})`;
            this.mixedCircle.style.color = this.getContrastColor(r, g, b);
        }
    }

    getContrastColor(r, g, b) {
        // Calculate relative luminance
        const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
        return luminance > 0.5 ? '#000000' : '#ffffff';
    }

    spectrumToRGB(spectrum) {
        // CIE 1931 color matching functions
        const x = spectrum.reduce((sum, intensity, i) => sum + intensity * this.cieX[i], 0);
        const y = spectrum.reduce((sum, intensity, i) => sum + intensity * this.cieY[i], 0);
        const z = spectrum.reduce((sum, intensity, i) => sum + intensity * this.cieZ[i], 0);
        
        // Normalize XYZ values
        const sum = x + y + z;
        const xyz = {
            x: x / sum,
            y: y / sum,
            z: z / sum
        };

        // Convert XYZ to RGB using sRGB transformation matrix
        const r = 3.2406 * xyz.x - 1.5372 * xyz.y - 0.4986 * xyz.z;
        const g = -0.9689 * xyz.x + 1.8758 * xyz.y + 0.0415 * xyz.z;
        const b = 0.0557 * xyz.x - 0.2040 * xyz.y + 1.0570 * xyz.z;

        // Gamma correction and clamping
        const gamma = 2.4;
        return [
            Math.max(0, Math.min(255, Math.pow(Math.max(0, r), 1/gamma) * 255)),
            Math.max(0, Math.min(255, Math.pow(Math.max(0, g), 1/gamma) * 255)),
            Math.max(0, Math.min(255, Math.pow(Math.max(0, b), 1/gamma) * 255))
        ];
    }
}

// Initialize when window loads
window.addEventListener('load', () => {
    console.log('Window loaded, initializing SpectralMixer');
    window.spectralMixer = new SpectralMixer();
}); 