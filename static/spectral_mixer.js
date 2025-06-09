// Spectral color mixing implementation
class SpectralMixer {
    constructor() {
        console.log('Initializing SpectralMixer');
        
        // Wavelength range (380-750nm)
        this.wavelengths = Array.from({length: 38}, (_, i) => i * 10 + 380);
        
        // Initialize spectral data for each pigment
        this.pigmentSpectra = {
            red: {
                name: 'Bengal Rose (PR169)',
                // Real pigment reflectance data
                reflectances: this.wavelengths.map(w => {
                    if (w < 500) return 0.15;
                    if (w < 550) return 0.25;
                    if (w < 600) return 0.30;
                    if (w < 650) return 0.85;
                    return 0.95;
                }),
                tintingStrength: 0.8
            },
            yellow: {
                name: 'Cadmium Yellow (PY35)',
                reflectances: this.wavelengths.map(w => {
                    if (w < 450) return 0.10;
                    if (w < 500) return 0.15;
                    if (w < 600) return 0.95;
                    if (w < 650) return 0.90;
                    return 0.85;
                }),
                tintingStrength: 0.6
            },
            blue: {
                name: 'Phthalo Blue (PB15)',
                reflectances: this.wavelengths.map(w => {
                    if (w < 500) return 0.90;
                    if (w < 550) return 0.20;
                    if (w < 600) return 0.15;
                    return 0.10;
                }),
                tintingStrength: 0.9
            },
            orange: {
                name: 'Cadmium Orange (PO20)',
                reflectances: this.wavelengths.map(w => {
                    if (w < 450) return 0.15;
                    if (w < 500) return 0.20;
                    if (w < 550) return 0.85;
                    if (w < 600) return 0.90;
                    if (w < 650) return 0.85;
                    return 0.80;
                }),
                tintingStrength: 0.7
            },
            brown: {
                name: 'Raw Umber (PBr7)',
                reflectances: this.wavelengths.map(w => {
                    if (w < 450) return 0.20;
                    if (w < 500) return 0.25;
                    if (w < 550) return 0.30;
                    if (w < 600) return 0.35;
                    if (w < 650) return 0.40;
                    return 0.45;
                }),
                tintingStrength: 0.5
            }
        };

        this.dropCounts = { red: 0, yellow: 0, blue: 0, orange: 0, brown: 0 };
        this.initializeControls();
        this.initializePlots();
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

            if (buttons.plus && buttons.minus && buttons.circle) {
                buttons.plus.addEventListener('click', () => {
                    this.dropCounts[color]++;
                    buttons.circle.textContent = this.dropCounts[color];
                    this.updateColors();
                    this.updatePlots();
                });

                buttons.minus.addEventListener('click', () => {
                    if (this.dropCounts[color] > 0) {
                        this.dropCounts[color]--;
                        buttons.circle.textContent = this.dropCounts[color];
                        this.updateColors();
                        this.updatePlots();
                    }
                });

                // Set initial color
                const rgb = this.spectrumToRGB(this.pigmentSpectra[color].reflectances);
                const [r, g, b] = rgb.map(Math.round);
                buttons.circle.style.backgroundColor = `rgb(${r}, ${g}, ${b})`;
                buttons.circle.style.color = this.getContrastColor(r, g, b);
                buttons.circle.textContent = '0';
            }
        });

        // Initialize mixed color circle
        this.mixedCircle = document.querySelector('.mixed-color .color-circle');
        if (this.mixedCircle) {
            this.updateColors();
        }
    }

    initializePlots() {
        // Initialize individual color plots
        Object.entries(this.pigmentSpectra).forEach(([color, data]) => {
            const plotDiv = document.getElementById(`${color}Spectrum`);
            if (!plotDiv) return;

            const rgb = this.spectrumToRGB(data.reflectances);
            const [r, g, b] = rgb.map(Math.round);

            const trace = {
                x: this.wavelengths,
                y: data.reflectances,
                type: 'scatter',
                mode: 'lines',
                name: data.name,
                line: {
                    color: `rgb(${r}, ${g}, ${b})`,
                    width: 2
                }
            };

            const layout = {
                title: `${data.name} Spectrum`,
                xaxis: {
                    title: 'Wavelength (nm)',
                    range: [380, 750],
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
                    range: [380, 750],
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

    calculateMixedSpectrum() {
        // Calculate total drops
        const totalDrops = Object.values(this.dropCounts).reduce((a, b) => a + b, 0);
        if (totalDrops === 0) {
            return Array(this.wavelengths.length).fill(1); // White when no pigments
        }

        // Initialize mixed spectrum with white (all 1s)
        let mixedSpectrum = Array(this.wavelengths.length).fill(1);
        
        // Apply Kubelka-Munk mixing for each pigment
        Object.entries(this.pigmentSpectra).forEach(([color, data]) => {
            const drops = this.dropCounts[color];
            if (drops > 0) {
                const weight = drops / totalDrops;
                const tintingFactor = Math.pow(data.tintingStrength, weight);
                
                // Apply Kubelka-Munk mixing formula
                for (let i = 0; i < this.wavelengths.length; i++) {
                    const R = data.reflectances[i];
                    const K = (1 - R) * (1 - R) / (2 * R); // Kubelka-Munk K/S
                    mixedSpectrum[i] *= Math.exp(-K * tintingFactor);
                }
            }
        });

        return mixedSpectrum;
    }

    spectrumToRGB(spectrum) {
        // Convert spectrum to Spectral.js color for accurate color space conversion
        const color = new spectral.Color();
        color.R = spectrum;
        return color.sRGB.map(x => x * 255);
    }

    updateColors() {
        const mixedSpectrum = this.calculateMixedSpectrum();
        const rgb = this.spectrumToRGB(mixedSpectrum);
        const [r, g, b] = rgb.map(Math.round);
        
        if (this.mixedCircle) {
            this.mixedCircle.style.backgroundColor = `rgb(${r}, ${g}, ${b})`;
            this.mixedCircle.style.color = this.getContrastColor(r, g, b);
        }
    }

    updatePlots() {
        const mixedPlotDiv = document.getElementById('mixedSpectrum');
        if (!mixedPlotDiv) return;

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

    getContrastColor(r, g, b) {
        const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
        return luminance > 0.5 ? '#000000' : '#ffffff';
    }
}

// Initialize when window loads
window.addEventListener('load', () => {
    console.log('Window loaded, initializing SpectralMixer');
    window.spectralMixer = new SpectralMixer();
});

// Initialize the spectral mixer when the page loads
document.addEventListener('DOMContentLoaded', function() {
    console.log('Initializing spectral mixer...');
    console.log('Spectrum plots:', spectrum_plots);
    
    // Initialize plots for each color
    const colors = ['red', 'yellow', 'blue', 'orange', 'brown', 'green', 'purple'];
    
    colors.forEach(color => {
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

        // Set initial color of the circle
        const circle = document.getElementById(`${color}Circle`);
        if (circle) {
            circle.style.backgroundColor = `rgb(${r}, ${g}, ${b})`;
            circle.style.color = getContrastColor(r, g, b);
        }
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

    // Initialize drop counts
    const dropCounts = {};
    colors.forEach(color => {
        dropCounts[color] = 0;
    });

    // Add event listeners for plus/minus buttons
    colors.forEach(color => {
        const plusBtn = document.getElementById(`${color}Plus`);
        const minusBtn = document.getElementById(`${color}Minus`);
        const circle = document.getElementById(`${color}Circle`);

        if (plusBtn && minusBtn && circle) {
            plusBtn.addEventListener('click', () => {
                console.log(`${color} plus button clicked`);
                dropCounts[color]++;
                circle.textContent = dropCounts[color];
                updateMixedColor();
            });

            minusBtn.addEventListener('click', () => {
                if (dropCounts[color] > 0) {
                    dropCounts[color]--;
                    circle.textContent = dropCounts[color];
                    updateMixedColor();
                }
            });
        }
    });

    // Initial update of mixed color
    updateMixedColor();
});

function updateMixedColor() {
    console.log('Updating mixed color...');
    // Get current drop counts
    const colors = ['red', 'yellow', 'blue', 'orange', 'brown', 'green', 'purple'];
    const dropCounts = {};
    colors.forEach(color => {
        const circle = document.getElementById(`${color}Circle`);
        if (circle) {
            dropCounts[color] = parseInt(circle.textContent) || 0;
        }
    });

    console.log('Drop counts:', dropCounts);

    // Send request to server
    fetch('/mix_colors', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ dropCounts })
    })
    .then(response => response.json())
    .then(data => {
        console.log('Received data:', data);
        const [r, g, b] = data.rgb;
        
        // Update mixed color circle
        const mixedCircle = document.getElementById('mixedCircle');
        if (mixedCircle) {
            mixedCircle.style.backgroundColor = `rgb(${r}, ${g}, ${b})`;
            mixedCircle.style.color = getContrastColor(r, g, b);
        }

        // Update mixed spectrum plot
        const mixedPlotDiv = document.getElementById('mixedSpectrum');
        if (mixedPlotDiv) {
            const trace = {
                x: data.spectrum.wavelengths,
                y: data.spectrum.reflectances,
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
    })
    .catch(error => console.error('Error:', error));
}

function getContrastColor(r, g, b) {
    // Calculate relative luminance
    const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    return luminance > 0.5 ? '#000000' : '#ffffff';
} 