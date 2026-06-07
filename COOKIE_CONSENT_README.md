# GDPR Cookie Consent System

This document explains the GDPR-compliant cookie consent system implemented in the ShadeMatch application.

## Overview

The cookie consent system provides a comprehensive solution for GDPR compliance, including:

**Important Notes:**
- This application is intended for individuals aged 18 and above
- Marketing cookies are not used in this application

- **Cookie Banner**: Displays when users first visit the site
- **Cookie Settings Modal**: Allows granular control over cookie categories
- **Privacy Policy**: Comprehensive privacy policy page
- **Backend Integration**: Server-side consent tracking and management

## Features

### Cookie Categories

1. **Necessary Cookies** (Always enabled)
   - Session management
   - Security tokens
   - User authentication
   - Essential functionality

2. **Analytics Cookies** (Optional)
   - Website usage statistics
   - Performance metrics
   - Error tracking
   - User behavior analysis


3. **Preference Cookies** (Optional)
   - Display preferences
   - Language settings
   - Theme preferences
   - User customizations

### User Interface

- **Banner**: Appears at the bottom of the page with clear options
- **Settings Modal**: Detailed cookie category management
- **Responsive Design**: Works on all device sizes
- **Accessibility**: Keyboard navigation and screen reader support

## Implementation

### Files Added

1. **`static/cookie-consent.css`** - Styling for the cookie consent UI
2. **`static/cookie-consent.js`** - JavaScript functionality and management
3. **`templates/base.html`** - Base template with cookie consent integration
4. **`templates/privacy_policy.html`** - GDPR-compliant privacy policy
5. **Updated templates** - All existing templates now extend the base template

### Backend Routes

- **`GET /privacy-policy`** - Privacy policy page
- **`POST /cookie-consent`** - Save user consent preferences
- **`GET /cookie-consent`** - Get current consent status

## Usage

### For Users

1. **First Visit**: Cookie banner appears automatically
2. **Accept All**: Enables all cookie categories
3. **Accept**: Enables only necessary cookies
4. **Decline**: Disables all optional cookies
5. **Settings**: Opens detailed cookie management modal

### For Developers

#### Checking Consent Status

```javascript
// Check if user has given consent for analytics
if (window.cookieConsent.canUseAnalytics()) {
    // Initialize Google Analytics or other analytics
    gtag('config', 'GA_MEASUREMENT_ID');
}

```

#### Listening for Consent Changes

```javascript
// Listen for consent updates
document.addEventListener('cookieConsentUpdated', function(event) {
    const consent = event.detail;
    console.log('Cookie consent updated:', consent);
    
    // Update services based on new consent
    if (consent.categories.analytics) {
        enableAnalytics();
    } else {
        disableAnalytics();
    }
});
```

#### Resetting Consent

```javascript
// Reset user consent (useful for testing)
window.cookieConsent.resetConsent();
```

## Configuration

### Customizing Cookie Categories

Edit the `cookieCategories` object in `static/cookie-consent.js`:

```javascript
this.cookieCategories = {
    necessary: {
        name: 'Necessary Cookies',
        description: 'Essential for website functionality',
        required: true,
        cookies: ['session_id', 'csrf_token']
    },
    analytics: {
        name: 'Analytics Cookies',
        description: 'Help us understand website usage',
        required: false,
        cookies: ['_ga', '_gid']
    },
    preferences: {
        name: 'Preference Cookies',
        description: 'Remember your settings',
        required: false,
        cookies: ['theme_preference', 'language_setting']
    }
    // Note: Marketing cookies are not used in this application
};
```

### Customizing Options

When initializing the cookie consent manager:

```javascript
window.cookieConsent = new CookieConsentManager({
    cookieName: 'your_app_cookie_consent',
    cookieExpiry: 365, // days
    showBanner: true,
    showSettings: true,
    privacyPolicyUrl: '/your-privacy-policy'
});
```

## GDPR Compliance Features

### Legal Requirements Met

1. **Explicit Consent**: Users must actively choose their preferences
2. **Granular Control**: Separate consent for different cookie categories
3. **Easy Withdrawal**: Users can change preferences at any time
4. **Clear Information**: Detailed descriptions of each cookie category
5. **Privacy Policy**: Comprehensive privacy policy linked from banner
6. **Audit Trail**: Server-side logging of consent decisions

### Data Protection

- **Minimal Data Collection**: Only necessary data is collected
- **Secure Storage**: Consent data is stored securely
- **Data Retention**: Clear retention policies
- **User Rights**: Full GDPR rights implementation

## Testing

### Manual Testing

1. Clear browser cookies
2. Visit the application
3. Verify cookie banner appears
4. Test all consent options
5. Verify settings modal functionality
6. Check privacy policy page

### Automated Testing

The system can be tested programmatically:

```javascript
// Test consent functionality
const consentManager = new CookieConsentManager();
consentManager.resetConsent(); // Clear existing consent
// Test banner appearance and functionality
```

## Browser Support

- **Modern Browsers**: Chrome, Firefox, Safari, Edge (latest versions)
- **Mobile Browsers**: iOS Safari, Chrome Mobile, Samsung Internet
- **Accessibility**: Screen readers, keyboard navigation
- **Fallbacks**: Graceful degradation for older browsers

## Security Considerations

1. **CSRF Protection**: All consent endpoints are protected
2. **Data Validation**: Server-side validation of consent data
3. **Secure Cookies**: HttpOnly and Secure flags where appropriate
4. **Audit Logging**: All consent decisions are logged

## Maintenance

### Regular Updates

1. **Review Cookie Categories**: Update as new services are added
2. **Privacy Policy**: Keep privacy policy current with regulations
3. **Legal Compliance**: Monitor GDPR and other privacy law changes
4. **User Feedback**: Collect and address user concerns

### Monitoring

- Monitor consent rates and user preferences
- Track privacy policy page visits
- Monitor for any consent-related errors
- Regular security audits

## Troubleshooting

### Common Issues

1. **Banner Not Appearing**: Check if consent cookie exists
2. **Settings Modal Not Working**: Verify JavaScript is loaded
3. **Privacy Policy 404**: Ensure route is properly configured
4. **Consent Not Saving**: Check server-side route functionality

### Debug Mode

Enable debug logging:

```javascript
// Add to cookie-consent.js for debugging
console.log('Cookie consent debug mode enabled');
```

## Support

For issues or questions regarding the cookie consent system:

- **Email**: konig.janos@semmelweis.hu
- **Subject**: "SHADEMATCH Cookie Consent"
- **Documentation**: This README file
- **Code**: All source files are well-commented

## Version History

- **v1.0** (December 2024): Initial GDPR-compliant implementation
  - Cookie banner with multiple consent options
  - Detailed settings modal
  - Privacy policy page
  - Backend integration
  - Responsive design
  - Accessibility features
