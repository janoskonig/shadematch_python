// Spectral color mixing implementation
class SpectralMixer {
    constructor() {
        this.wavelengths = Array.from({length: 381}, (_, i) => i + 380); // 380-760nm
        this.initializeControls();
        this.initializePlot();
    }

    // Initialize UI controls
    initializeControls() {
        // Red controls
        this.redWavelength = document.getElementById('redWavelength');
        this.redIntensity = document.getElementById('redIntensity');
        this.redPreview = document.getElementById('redPreview');

        // Green controls
        this.greenWavelength = document.getElementById('greenWavelength');
        this.greenIntensity = document.getElementById('greenIntensity');
        this.greenPreview = document.getElementById('greenPreview');

        // Blue controls
        this.blueWavelength = document.getElementById('blueWavelength');
        this.blueIntensity = document.getElementById('blueIntensity');
        this.bluePreview = document.getElementById('bluePreview');

        // Buttons
        this.mixButton = document.getElementById('mixButton');
        this.resetButton = document.getElementById('resetButton');

        // Add event listeners
        this.addEventListeners();
    }

    // Add event listeners to controls
    addEventListeners() {
        // Red controls
        this.redWavelength.addEventListener('input', () => this.updateColor('red'));
        this.redIntensity.addEventListener('input', () => this.updateColor('red'));

        // Green controls
        this.greenWavelength.addEventListener('input', () => this.updateColor('green'));
        this.greenIntensity.addEventListener('input', () => this.updateColor('green'));

        // Blue controls
        this.blueWavelength.addEventListener('input', () => this.updateColor('blue'));
        this.blueIntensity.addEventListener('input', () => this.updateColor('blue'));

        // Buttons
        this.mixButton.addEventListener('click', () => this.mixColors());
        this.resetButton.addEventListener('click', () => this.reset());
    }

    // Initialize the spectrum plot
    initializePlot() {
        const layout = {
            title: 'Spectral Distribution',
            xaxis: {
                title: 'Wavelength (nm)',
                range: [380, 760]
            },
            yaxis: {
                title: 'Intensity',
                range: [0, 1]
            },
            paper_bgcolor: 'rgba(0,0,0,0)',
            plot_bgcolor: 'rgba(0,0,0,0)',
            font: {
                color: 'white'
            }
        };

        Plotly.newPlot('spectrumDisplay', [], layout);
    }

    // Update color preview and spectrum
    updateColor(color) {
        const wavelength = parseInt(this[`${color}Wavelength`].value);
        const intensity = parseInt(this[`${color}Intensity`].value) / 100;

        // Update color preview
        const rgb = this.wavelengthToRGB(wavelength);
        this[`${color}Preview`].style.backgroundColor = `rgb(${rgb.join(',')})`;

        // Update spectrum plot
        this.updateSpectrum();
    }

    // Convert wavelength to RGB
    wavelengthToRGB(wavelength) {
        let r, g, b;

        if (wavelength >= 380 && wavelength < 440) {
            r = -(wavelength - 440) / (440 - 380);
            g = 0;
            b = 1;
        } else if (wavelength >= 440 && wavelength < 490) {
            r = 0;
            g = (wavelength - 440) / (490 - 440);
            b = 1;
        } else if (wavelength >= 490 && wavelength < 510) {
            r = 0;
            g = 1;
            b = -(wavelength - 510) / (510 - 490);
        } else if (wavelength >= 510 && wavelength < 580) {
            r = (wavelength - 510) / (580 - 510);
            g = 1;
            b = 0;
        } else if (wavelength >= 580 && wavelength < 645) {
            r = 1;
            g = -(wavelength - 645) / (645 - 580);
            b = 0;
        } else if (wavelength >= 645 && wavelength <= 780) {
            r = 1;
            g = 0;
            b = 0;
        } else {
            r = 0;
            g = 0;
            b = 0;
        }

        // Adjust for intensity
        const intensity = parseInt(this[`${wavelength >= 645 ? 'red' : wavelength >= 580 ? 'red' : wavelength >= 510 ? 'green' : wavelength >= 490 ? 'green' : wavelength >= 440 ? 'blue' : 'blue'}Intensity`].value) / 100;

        return [
            Math.round(r * 255 * intensity),
            Math.round(g * 255 * intensity),
            Math.round(b * 255 * intensity)
        ];
    }

    // Generate Gaussian distribution for a wavelength
    generateGaussian(wavelength, intensity, fwhm = 30) {
        return this.wavelengths.map(w => {
            const sigma = fwhm / (2 * Math.sqrt(2 * Math.log(2)));
            return intensity * Math.exp(-Math.pow(w - wavelength, 2) / (2 * Math.pow(sigma, 2)));
        });
    }

    // Update the spectrum plot
    updateSpectrum() {
        const redSpectrum = this.generateGaussian(
            parseInt(this.redWavelength.value),
            parseInt(this.redIntensity.value) / 100
        );
        const greenSpectrum = this.generateGaussian(
            parseInt(this.greenWavelength.value),
            parseInt(this.greenIntensity.value) / 100
        );
        const blueSpectrum = this.generateGaussian(
            parseInt(this.blueWavelength.value),
            parseInt(this.blueIntensity.value) / 100
        );

        const traces = [
            {
                x: this.wavelengths,
                y: redSpectrum,
                name: 'Red',
                line: { color: 'red' }
            },
            {
                x: this.wavelengths,
                y: greenSpectrum,
                name: 'Green',
                line: { color: 'green' }
            },
            {
                x: this.wavelengths,
                y: blueSpectrum,
                name: 'Blue',
                line: { color: 'blue' }
            }
        ];

        Plotly.react('spectrumDisplay', traces);
    }

    // Mix the colors
    mixColors() {
        const redSpectrum = this.generateGaussian(
            parseInt(this.redWavelength.value),
            parseInt(this.redIntensity.value) / 100
        );
        const greenSpectrum = this.generateGaussian(
            parseInt(this.greenWavelength.value),
            parseInt(this.greenIntensity.value) / 100
        );
        const blueSpectrum = this.generateGaussian(
            parseInt(this.blueWavelength.value),
            parseInt(this.blueIntensity.value) / 100
        );

        // Mix the spectra
        const mixedSpectrum = this.wavelengths.map((_, i) => 
            redSpectrum[i] + greenSpectrum[i] + blueSpectrum[i]
        );

        // Normalize the mixed spectrum
        const maxIntensity = Math.max(...mixedSpectrum);
        const normalizedSpectrum = mixedSpectrum.map(i => i / maxIntensity);

        // Calculate the resulting RGB color
        const rgb = this.spectrumToRGB(normalizedSpectrum);
        
        // Update the result display
        document.getElementById('resultColor').style.backgroundColor = `rgb(${rgb.join(',')})`;

        // Plot the mixed spectrum
        const trace = {
            x: this.wavelengths,
            y: normalizedSpectrum,
            name: 'Mixed Spectrum',
            line: { color: `rgb(${rgb.join(',')})` }
        };

        Plotly.newPlot('mixedSpectrum', [trace], {
            title: 'Mixed Spectrum',
            xaxis: { title: 'Wavelength (nm)' },
            yaxis: { title: 'Intensity' },
            paper_bgcolor: 'rgba(0,0,0,0)',
            plot_bgcolor: 'rgba(0,0,0,0)',
            font: { color: 'white' }
        });
    }

    // Convert spectrum to RGB
    spectrumToRGB(spectrum) {
        // CIE color matching functions (simplified)
        const x = spectrum.reduce((sum, intensity, i) => sum + intensity * this.cieX[i], 0);
        const y = spectrum.reduce((sum, intensity, i) => sum + intensity * this.cieY[i], 0);
        const z = spectrum.reduce((sum, intensity, i) => sum + intensity * this.cieZ[i], 0);

        // Convert XYZ to RGB
        const r = 3.2406 * x - 1.5372 * y - 0.4986 * z;
        const g = -0.9689 * x + 1.8758 * y + 0.0415 * z;
        const b = 0.0557 * x - 0.2040 * y + 1.0570 * z;

        // Normalize and clamp
        return [
            Math.round(Math.max(0, Math.min(255, r * 255))),
            Math.round(Math.max(0, Math.min(255, g * 255))),
            Math.round(Math.max(0, Math.min(255, b * 255)))
        ];
    }

    // Reset all controls
    reset() {
        this.redWavelength.value = 650;
        this.redIntensity.value = 100;
        this.greenWavelength.value = 550;
        this.greenIntensity.value = 100;
        this.blueWavelength.value = 450;
        this.blueIntensity.value = 100;

        this.updateColor('red');
        this.updateColor('green');
        this.updateColor('blue');
        this.mixColors();
    }

    // CIE color matching functions (simplified)
    cieX = Array(381).fill(0).map((_, i) => {
        const w = i + 380;
        if (w >= 380 && w <= 780) {
            return Math.exp(-0.5 * Math.pow((w - 580) / 100, 2));
        }
        return 0;
    });

    cieY = Array(381).fill(0).map((_, i) => {
        const w = i + 380;
        if (w >= 380 && w <= 780) {
            return Math.exp(-0.5 * Math.pow((w - 540) / 100, 2));
        }
        return 0;
    });

    cieZ = Array(381).fill(0).map((_, i) => {
        const w = i + 380;
        if (w >= 380 && w <= 780) {
            return Math.exp(-0.5 * Math.pow((w - 440) / 100, 2));
        }
        return 0;
    });
}

// Initialize the app when the DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    const mixer = new SpectralMixer();
    mixer.reset();
}); 