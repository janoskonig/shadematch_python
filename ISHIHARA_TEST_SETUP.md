# Ishihara Color Vision Test Setup Guide

## Overview
Your Flask app now includes a comprehensive 25-plate Ishihara color vision test with:
- Single-choice quiz format
- PNG image plates
- CSV-based test configuration
- Timer and scoring
- Professional UI/UX

## File Structure
```
static/
├── ishihara_test_data.csv          # Test configuration (answers, options, questions)
├── plates/
│   ├── README.md                   # Plate specifications
│   ├── ishihara_01.png            # Plate 1 (shows number 12)
│   ├── ishihara_02.png            # Plate 2 (shows number 8)
│   └── ...                        # ... up to ishihara_25.png
templates/
└── ishihara_test.html             # Test interface
app/
└── routes.py                      # Flask route for /ishihara-test
```

## Setup Steps

### 1. Generate Placeholder Images (Optional)
For testing purposes, you can generate simple placeholder images:

```bash
# Install dependencies
pip install -r requirements_images.txt

# Generate placeholder plates
python generate_test_plates.py
```

This will create 25 simple PNG images in `static/plates/` for testing.

### 2. Replace with Real Ishihara Plates
Replace the placeholder images with actual Ishihara plates:
- **Format**: PNG (recommended for transparency)
- **Size**: 400x400 or 500x500 pixels
- **Style**: Traditional Ishihara plates with colored dots
- **Numbers**: Embedded within dot patterns using color contrast

### 3. Customize Test Configuration
Edit `static/ishihara_test_data.csv` to:
- Change answers
- Modify multiple choice options
- Update question text
- Reorder plates

## CSV Format
```csv
plate_id,image_file,correct_answer,option_1,option_2,option_3,option_4,question_text
1,ishihara_01.png,12,12,8,29,15,What number do you see?
```

## Features

### Test Interface
- **Progress Bar**: Shows completion status
- **Timer**: Tracks total test time
- **Navigation**: Previous/Next buttons
- **Single Choice**: 4 options per plate
- **Responsive Design**: Works on mobile and desktop

### Scoring System
- **Percentage Score**: Based on correct answers
- **Time Tracking**: Total time and average per plate
- **Result Categories**: 
  - 90%+: Excellent color vision
  - 80-89%: Good with minor difficulties
  - 70-79%: Moderate deficiency
  - 60-69%: Significant deficiency
  - <60%: Severe deficiency

### User Experience
- **Loading States**: Smooth transitions
- **Error Handling**: Graceful fallbacks for missing images
- **Accessibility**: Clear visual feedback
- **Mobile Friendly**: Responsive design

## Usage

### For Users
1. Navigate to `/ishihara-test`
2. View each plate and select the number you see
3. Use Previous/Next to navigate
4. Complete all 25 plates
5. View detailed results and recommendations

### For Administrators
1. **Update Plates**: Replace PNG files in `static/plates/`
2. **Modify Test**: Edit `ishihara_test_data.csv`
3. **Customize UI**: Modify `templates/ishihara_test.html`
4. **Add Routes**: Extend `app/routes.py` as needed

## Integration

### Main App Navigation
The test is accessible via:
- Direct URL: `/ishihara-test`
- Navigation menu (if added to main page)
- Registration flow (redirects to test)

### Data Storage
Currently stores results in session. To persist results:
1. Add database models for test results
2. Modify routes to save scores
3. Add user progress tracking

## Customization Options

### Visual Design
- Modify CSS in `templates/ishihara_test.html`
- Change color schemes and fonts
- Adjust plate sizes and layouts

### Test Logic
- Modify scoring algorithms
- Add different question types
- Implement adaptive difficulty

### Data Management
- Add result export functionality
- Implement user progress tracking
- Add admin panel for test management

## Testing

### Local Development
```bash
# Run Flask app
python run.py

# Visit http://localhost:5000/ishihara-test
```

### Production Deployment
- Ensure all PNG files are properly sized and optimized
- Test on different devices and browsers
- Validate CSV data format
- Check image loading performance

## Troubleshooting

### Common Issues
1. **Images Not Loading**: Check file paths and permissions
2. **CSV Parse Errors**: Verify CSV format and encoding
3. **Test Not Starting**: Check browser console for JavaScript errors
4. **Responsive Issues**: Test on different screen sizes

### Debug Mode
Enable Flask debug mode to see detailed error messages:
```python
app.run(debug=True)
```

## Future Enhancements

### Potential Improvements
- **Audio Instructions**: Voice guidance for accessibility
- **Practice Mode**: Sample plates before actual test
- **Detailed Analysis**: Plate-by-plate performance review
- **Export Results**: PDF reports or data export
- **Multi-language**: Internationalization support
- **Advanced Scoring**: Weighted scoring based on plate difficulty

### Integration Ideas
- **User Management**: Individual user accounts and progress
- **Research Tools**: Data collection for studies
- **Mobile App**: Native mobile application
- **API Access**: RESTful API for external integrations

## Support
For issues or questions:
1. Check browser console for JavaScript errors
2. Verify file paths and permissions
3. Test with placeholder images first
4. Review Flask application logs

---

**Note**: This system is designed to be flexible and maintainable. The CSV-based configuration allows easy updates without code changes, while the PNG image format ensures high-quality, professional test plates.

