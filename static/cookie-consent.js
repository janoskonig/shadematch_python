/**
 * GDPR Cookie Consent Manager
 * A comprehensive cookie consent solution for GDPR compliance
 */

class CookieConsentManager {
    constructor(options = {}) {
        this.options = {
            // Default configuration
            cookieName: 'cookie_consent',
            cookieExpiry: 365, // days
            showBanner: true,
            showSettings: true,
            privacyPolicyUrl: '/privacy-policy',
            ...options
        };

        this.cookieCategories = {
            necessary: {
                name: 'Necessary Cookies',
                description: 'These cookies are essential for the website to function properly. They cannot be disabled.',
                required: true,
                cookies: ['session_id', 'csrf_token', 'user_preferences']
            },
            analytics: {
                name: 'Analytics Cookies',
                description: 'These cookies help us understand how visitors interact with our website by collecting and reporting information anonymously.',
                required: false,
                cookies: ['_ga', '_gid', '_gat', 'analytics_session']
            },
            preferences: {
                name: 'Preference Cookies',
                description: 'These cookies remember your choices and preferences to provide a more personalized experience.',
                required: false,
                cookies: ['theme_preference', 'language_setting', 'display_options']
            }
        };

        this.consent = this.loadConsent();
        this.init();
    }

    init() {
        // Check if consent has been given
        if (!this.consent.given) {
            this.showBanner();
        } else {
            this.applyConsent();
        }

        // Add event listeners
        this.addEventListeners();
    }

    loadConsent() {
        const cookieValue = this.getCookie(this.options.cookieName);
        if (cookieValue) {
            try {
                return JSON.parse(decodeURIComponent(cookieValue));
            } catch (e) {
                console.warn('Invalid cookie consent data, resetting...');
            }
        }
        
        return {
            given: false,
            timestamp: null,
            categories: {
                necessary: true, // Always true
                analytics: false,
                preferences: false
            }
        };
    }

    saveConsent(categories) {
        const consent = {
            given: true,
            timestamp: new Date().toISOString(),
            categories: {
                necessary: true, // Always true
                analytics: categories.analytics || false,
                preferences: categories.preferences || false
            }
        };

        this.consent = consent;
        this.setCookie(this.options.cookieName, JSON.stringify(consent), this.options.cookieExpiry);
        
        // Send consent to server for audit purposes
        this.sendConsentToServer(consent);
        
        this.applyConsent();
        this.hideBanner();
    }

    async sendConsentToServer(consent) {
        try {
            const response = await fetch('/cookie-consent', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ consent: consent })
            });
            
            if (!response.ok) {
                console.warn('Failed to save consent to server:', response.statusText);
            }
        } catch (error) {
            console.warn('Error sending consent to server:', error);
        }
    }

    applyConsent() {
        // Apply consent to different services
        this.applyAnalyticsConsent();
        this.applyPreferenceConsent();
        
        // Dispatch custom event
        document.dispatchEvent(new CustomEvent('cookieConsentUpdated', {
            detail: this.consent
        }));
    }

    applyAnalyticsConsent() {
        if (this.consent.categories.analytics) {
            // Enable Google Analytics or other analytics
            this.enableAnalytics();
        } else {
            // Disable analytics
            this.disableAnalytics();
        }
    }


    applyPreferenceConsent() {
        if (this.consent.categories.preferences) {
            // Enable preference cookies
            this.enablePreferences();
        } else {
            // Disable preference cookies
            this.disablePreferences();
        }
    }

    enableAnalytics() {
        // Example: Enable Google Analytics
        if (typeof gtag !== 'undefined') {
            gtag('consent', 'update', {
                'analytics_storage': 'granted'
            });
        }
        
        // Enable other analytics services
        console.log('Analytics cookies enabled');
    }

    disableAnalytics() {
        // Example: Disable Google Analytics
        if (typeof gtag !== 'undefined') {
            gtag('consent', 'update', {
                'analytics_storage': 'denied'
            });
        }
        
        // Disable other analytics services
        console.log('Analytics cookies disabled');
    }


    enablePreferences() {
        // Enable preference cookies
        console.log('Preference cookies enabled');
    }

    disablePreferences() {
        // Disable preference cookies
        console.log('Preference cookies disabled');
    }

    showBanner() {
        if (!this.options.showBanner) return;

        const banner = this.createBanner();
        document.body.appendChild(banner);
        
        // Show banner with animation
        setTimeout(() => {
            banner.classList.add('show');
        }, 100);
    }

    hideBanner() {
        const banner = document.querySelector('.cookie-consent-banner');
        if (banner) {
            banner.classList.remove('show');
            setTimeout(() => {
                banner.remove();
            }, 300);
        }
    }

    createBanner() {
        const banner = document.createElement('div');
        banner.className = 'cookie-consent-banner';
        banner.innerHTML = `
            <div class="cookie-consent-content">
                <div class="cookie-consent-text">
                    <h3>🍪 Cookie Consent</h3>
                    <p>
                        We use cookies to enhance your browsing experience, serve personalized content, 
                        and analyze our traffic. By clicking "Accept All", you consent to our use of cookies. 
                        <a href="${this.options.privacyPolicyUrl}" class="privacy-policy-link" target="_blank">Privacy Policy</a>
                    </p>
                </div>
                <div class="cookie-consent-buttons">
                    <button class="cookie-consent-btn cookie-consent-btn-decline" data-action="decline">
                        Decline
                    </button>
                    ${this.options.showSettings ? `
                        <button class="cookie-consent-btn cookie-consent-btn-settings" data-action="settings">
                            Settings
                        </button>
                    ` : ''}
                    <button class="cookie-consent-btn cookie-consent-btn-accept" data-action="accept">
                        Accept
                    </button>
                    <button class="cookie-consent-btn cookie-consent-btn-accept-all" data-action="accept-all">
                        Accept All
                    </button>
                </div>
            </div>
        `;
        return banner;
    }

    showSettings() {
        const modal = this.createSettingsModal();
        document.body.appendChild(modal);
        
        setTimeout(() => {
            modal.classList.add('show');
        }, 100);
    }

    createSettingsModal() {
        const modal = document.createElement('div');
        modal.className = 'cookie-settings-modal';
        modal.innerHTML = `
            <div class="cookie-settings-content">
                <div class="cookie-settings-header">
                    <h2>Cookie Settings</h2>
                    <button class="cookie-settings-close" data-action="close">&times;</button>
                </div>
                <div class="cookie-settings-body">
                    ${Object.entries(this.cookieCategories).map(([key, category]) => `
                        <div class="cookie-category">
                            <div class="cookie-category-header">
                                <h3 class="cookie-category-title">${category.name}</h3>
                                <label class="cookie-toggle">
                                    <input type="checkbox" 
                                           data-category="${key}" 
                                           ${category.required ? 'checked disabled' : (this.consent.categories[key] ? 'checked' : '')}>
                                    <span class="cookie-toggle-slider"></span>
                                </label>
                            </div>
                            <p class="cookie-category-description">${category.description}</p>
                        </div>
                    `).join('')}
                </div>
                <div class="cookie-settings-footer">
                    <button class="cookie-settings-btn cookie-settings-btn-cancel" data-action="cancel">
                        Cancel
                    </button>
                    <button class="cookie-settings-btn cookie-settings-btn-save" data-action="save">
                        Save Preferences
                    </button>
                </div>
            </div>
        `;
        return modal;
    }

    addEventListeners() {
        // Banner event listeners
        document.addEventListener('click', (e) => {
            if (e.target.matches('[data-action="accept"]')) {
                this.saveConsent({
                    necessary: true,
                    analytics: false,
                    preferences: false
                });
            } else if (e.target.matches('[data-action="accept-all"]')) {
                this.saveConsent({
                    necessary: true,
                    analytics: true,
                    preferences: true
                });
            } else if (e.target.matches('[data-action="decline"]')) {
                this.saveConsent({
                    necessary: true,
                    analytics: false,
                    preferences: false
                });
            } else if (e.target.matches('[data-action="settings"]')) {
                this.showSettings();
            }
        });

        // Settings modal event listeners
        document.addEventListener('click', (e) => {
            if (e.target.matches('.cookie-settings-close, [data-action="close"], [data-action="cancel"]')) {
                this.closeSettings();
            } else if (e.target.matches('[data-action="save"]')) {
                this.saveSettings();
            }
        });

        // Close modal when clicking outside
        document.addEventListener('click', (e) => {
            if (e.target.classList.contains('cookie-settings-modal')) {
                this.closeSettings();
            }
        });
    }

    saveSettings() {
        const categories = {};
        const checkboxes = document.querySelectorAll('.cookie-settings-modal input[type="checkbox"]');
        
        checkboxes.forEach(checkbox => {
            const category = checkbox.dataset.category;
            categories[category] = checkbox.checked;
        });

        this.saveConsent(categories);
        this.closeSettings();
    }

    closeSettings() {
        const modal = document.querySelector('.cookie-settings-modal');
        if (modal) {
            modal.classList.remove('show');
            setTimeout(() => {
                modal.remove();
            }, 300);
        }
    }

    // Utility methods
    getCookie(name) {
        const value = `; ${document.cookie}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) return parts.pop().split(';').shift();
        return null;
    }

    setCookie(name, value, days) {
        const expires = new Date();
        expires.setTime(expires.getTime() + (days * 24 * 60 * 60 * 1000));
        document.cookie = `${name}=${encodeURIComponent(value)};expires=${expires.toUTCString()};path=/;SameSite=Lax`;
    }

    // Public API methods
    hasConsent(category) {
        return this.consent.given && this.consent.categories[category];
    }

    getConsent() {
        return this.consent;
    }

    resetConsent() {
        this.setCookie(this.options.cookieName, '', -1);
        this.consent = {
            given: false,
            timestamp: null,
            categories: {
                necessary: true,
                analytics: false,
                preferences: false
            }
        };
        this.showBanner();
    }

    // Method to check if user has given consent for specific functionality
    canUseAnalytics() {
        return this.hasConsent('analytics');
    }


    canUsePreferences() {
        return this.hasConsent('preferences');
    }
}

// Initialize cookie consent manager when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    // Initialize with custom options
    window.cookieConsent = new CookieConsentManager({
        cookieName: 'shadematch_cookie_consent',
        cookieExpiry: 365,
        showBanner: true,
        showSettings: true,
        privacyPolicyUrl: '/privacy-policy' // You can create this route
    });
});

// Export for module usage
if (typeof module !== 'undefined' && module.exports) {
    module.exports = CookieConsentManager;
}
