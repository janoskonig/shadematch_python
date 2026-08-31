(function () {
  'use strict';

  var config = window.SHARE_PAGE || {};
  var shareButton = document.getElementById('nativeShareButton');
  var copyButton = document.getElementById('copyShareLink');
  var status = document.getElementById('shareStatus');

  function showStatus(message, isError) {
    if (!status) return;
    status.textContent = message || '';
    status.style.color = isError ? 'var(--accent-danger)' : 'var(--accent-success)';
  }

  function fallbackCopy(text) {
    var input = document.createElement('textarea');
    input.value = text;
    input.setAttribute('readonly', '');
    input.style.position = 'fixed';
    input.style.opacity = '0';
    document.body.appendChild(input);
    input.select();
    var copied = document.execCommand('copy');
    input.remove();
    if (!copied) throw new Error('Copy command failed');
  }

  async function copyLink() {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(config.url);
      } else {
        fallbackCopy(config.url);
      }
      showStatus(config.copied, false);
    } catch (_error) {
      showStatus(config.copyFailed, true);
    }
  }

  if (copyButton) copyButton.addEventListener('click', copyLink);

  if (shareButton) {
    shareButton.addEventListener('click', async function () {
      if (navigator.share) {
        try {
          await navigator.share({ title: config.title, text: config.text, url: config.url });
          showStatus('', false);
          return;
        } catch (error) {
          if (error && error.name === 'AbortError') return;
        }
      }
      await copyLink();
    });
  }
})();
