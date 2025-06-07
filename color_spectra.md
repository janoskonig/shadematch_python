# Color Reflectance Spectra

This document contains the reflectance spectra for the primary colors used in the color mixing application. These spectra are based on standard color measurements and can be used for more accurate color mixing calculations.

## Red (255, 0, 0)

The red color used in the application has the following approximate reflectance spectrum:
- Wavelength (nm) | Reflectance (%)
- 400-500        | 10-15%
- 500-600        | 15-20%
- 600-700        | 85-95%
- 700-780        | 90-100%

## Blue (0, 0, 255)

The blue color used in the application has the following approximate reflectance spectrum:
- Wavelength (nm) | Reflectance (%)
- 400-500        | 85-95%
- 500-600        | 15-25%
- 600-700        | 10-15%
- 700-780        | 5-10%

## Yellow (255, 255, 0)

The yellow color used in the application has the following approximate reflectance spectrum:
- Wavelength (nm) | Reflectance (%)
- 400-500        | 15-25%
- 500-600        | 85-95%
- 600-700        | 80-90%
- 700-780        | 75-85%

## Notes

1. These spectra are approximate values based on standard color measurements
2. The actual reflectance may vary depending on:
   - The specific pigments or dyes used
   - The substrate material
   - The measurement conditions
   - The lighting conditions

## References

1. "Color Science: Concepts and Methods, Quantitative Data and Formulae" by Günther Wyszecki and W.S. Stiles
2. "Measuring Color" by R.W.G. Hunt
3. "Color Appearance Models" by Mark D. Fairchild

## Usage in the Application

These spectra can be used to:
1. Improve color mixing accuracy
2. Calculate more precise Delta E values
3. Better simulate real-world color mixing behavior
4. Account for metamerism in color matching

## Implementation Considerations

When implementing these spectra in the application:
1. Consider using a spectral color mixing model
2. Account for the illuminant's spectral power distribution
3. Use appropriate color space transformations
4. Consider implementing spectral rendering for more accurate results 