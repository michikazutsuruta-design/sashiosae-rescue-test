/* Draft: privacy-limited measurement helpers. No credentials, tracking ID or activation. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ConsultationMeasurement = factory();
}(typeof window === 'object' ? window : this, function () {
  'use strict';
  var pages = ['/', '/index.html', '/sashiosae-notice.html', '/keibai-sale.html',
    '/ninbai-guide.html', '/inherited-house.html', '/privacy.html'];
  var events = ['contact_click', 'form_start', 'form_submit_attempt',
    'form_submit_error', 'generate_lead'];
  function pageName(href) {
    try {
      var path = new URL(href).pathname.replace(/^\/sashiosae-rescue-test/, '') || '/';
      return pages.indexOf(path) >= 0 ? path : '/other';
    } catch (_) { return '/other'; }
  }
  function sourceGroup(href, referrer) {
    var allowed = ['instagram', 'threads', 'google', 'bing', 'linkedin'];
    try {
      var url = new URL(href), source = url.searchParams.get('utm_source');
      if (allowed.indexOf(source) >= 0) return source;
      if (!referrer) return 'direct_or_unknown';
      var host = new URL(referrer).hostname.toLowerCase();
      if (host === url.hostname) return 'internal';
      if (/(^|\.)instagram\.com$/.test(host)) return 'instagram';
      if (/(^|\.)threads\.(com|net)$/.test(host)) return 'threads';
      if (/(^|\.)google\.(com|co\.jp)$/.test(host)) return 'google';
      if (/(^|\.)bing\.com$/.test(host)) return 'bing';
      if (/(^|\.)linkedin\.com$/.test(host)) return 'linkedin';
      return 'other';
    } catch (_) { return 'direct_or_unknown'; }
  }
  function reporter(send, consent, context) {
    return function (name, linkKind) {
      if (!consent() || events.indexOf(name) < 0) return false;
      var data = {
        page_path: pageName(context.href),
        source_group: sourceGroup(context.href, context.referrer),
        form_id: 'consultation'
      };
      if (['form', 'phone', 'email'].indexOf(linkKind) >= 0) data.link_kind = linkKind;
      // Never forward form values, arbitrary URLs, queries, campaign text, or link text.
      try { send(name, data); return true; } catch (_) { return false; }
    };
  }
  function submissionController(post, report) {
    var pending = false, accepted = false;
    return async function (formData) {
      if (pending || accepted) return { state: 'duplicate_blocked' };
      pending = true;
      report('form_submit_attempt');
      try {
        var response = await post(formData);
        if (response.ok) {
          accepted = true;
          report('generate_lead');
          return { state: 'accepted' };
        }
        report('form_submit_error');
        return { state: 'rejected' };
      } catch (_) {
        // A network failure may follow server acceptance. Never retry automatically.
        report('form_submit_error');
        return { state: 'unknown' };
      } finally { pending = false; }
    };
  }
  return { pageName: pageName, sourceGroup: sourceGroup,
    reporter: reporter, submissionController: submissionController };
}));

/* Free GA4 conversion measurement. Google receives no form fields. */
(function () {
  'use strict';
  var core = window.ConsultationMeasurement;
  if (!core || !window.URL) return;
  var id = 'G-JYWJVDPLM9';
  var key = 'sashiosae_analytics_consent_v1';
  var allowed = false, started = false, choice = '';
  var context = { href: window.location.href, referrer: document.referrer };
  var source = core.sourceGroup(context.href, context.referrer);
  var page = core.pageName(context.href);
  var canonical = document.querySelector('link[rel="canonical"]');
  var location = canonical ? canonical.href : window.location.origin + window.location.pathname;
  // Tracking location and referrer never include URL queries, fragments or arbitrary paths.
  location = new URL(location).origin + new URL(location).pathname;
  var safeReferrer = '';
  try {
    if (['instagram', 'threads', 'google', 'bing', 'linkedin'].indexOf(core.sourceGroup(location, context.referrer)) >= 0 && context.referrer) {
      safeReferrer = new URL(context.referrer).origin + '/';
    }
  } catch (_) {}
  try {
    var saved = JSON.parse(localStorage.getItem(key));
    if (saved && Date.now() - saved.time < 180 * 86400000 && ['yes', 'no'].indexOf(saved.choice) >= 0) {
      choice = saved.choice;
    }
  } catch (_) {}
  function gtag() { window.dataLayer.push(arguments); }
  function start() {
    window['ga-disable-' + id] = false;
    if (started) return;
    started = true;
    window.dataLayer = window.dataLayer || [];
    gtag('consent', 'default', {
      analytics_storage: 'granted', ad_storage: 'denied',
      ad_user_data: 'denied', ad_personalization: 'denied'
    });
    gtag('js', new Date());
    gtag('config', id, {
      send_page_view: false, allow_google_signals: false,
      allow_ad_personalization_signals: false,
      page_location: location, page_referrer: safeReferrer,
      page_title: '差押えレスキュー'
    });
    gtag('event', 'page_view', {
      page_location: location, page_referrer: safeReferrer,
      page_title: '差押えレスキュー', source_group: source
    });
    var script = document.createElement('script');
    script.async = true;
    script.src = 'https://www.googletagmanager.com/gtag/js?id=' + id;
    document.head.appendChild(script);
  }
  function stop() {
    window['ga-disable-' + id] = true;
    // Remove only this site's Google Analytics cookies after withdrawal.
    document.cookie.split(';').forEach(function (entry) {
      var name = entry.split('=')[0].trim();
      if (!/^_ga(?:_|$)/.test(name)) return;
      ['/', '/sashiosae-rescue-test/'].forEach(function (path) {
        ['', '; domain=' + window.location.hostname, '; domain=.' + window.location.hostname].forEach(function (domain) {
          document.cookie = name + '=; max-age=0; path=' + path + domain + '; SameSite=Lax; Secure';
        });
      });
    });
  }
  var report = core.reporter(function (name, data) {
    data.page_location = location;
    data.page_referrer = safeReferrer;
    data.page_title = '差押えレスキュー';
    gtag('event', name, data);
  }, function () { return allowed; }, context);
  var panel = document.createElement('aside');
  panel.setAttribute('aria-label', '任意のアクセス解析設定');
  panel.style.cssText = 'max-width:1100px;margin:12px auto;padding:12px 20px;font-size:14px;line-height:1.7;background:#eef3f7;color:#203044;border-radius:7px';
  var text = document.createElement('p');
  text.style.margin = '0 0 8px';
  var explanation = 'サイト改善のため、Google アナリティクスで閲覧・相談ボタン・送信完了を計測してよいですか？氏名・メール・相談内容は送信しません。拒否しても無料相談を利用できます。';
  var accept = document.createElement('button'), reject = document.createElement('button'), edit = document.createElement('button');
  accept.textContent = '計測を許可する'; reject.textContent = '許可しない'; edit.textContent = '計測設定を変更';
  [accept, reject, edit].forEach(function (button) {
    button.type = 'button';
    button.style.cssText = 'width:auto;min-height:44px;margin:4px 10px 4px 0;padding:8px 14px;font-size:14px;border:1px solid #526b80;background:white;color:#203044;border-radius:5px;cursor:pointer';
    panel.appendChild(button);
  });
  var policy = document.createElement('a'); policy.href = 'privacy.html'; policy.textContent = '詳しい説明';
  policy.style.marginLeft = '6px'; panel.appendChild(policy); panel.insertBefore(text, accept);
  function renderChoice() {
    text.textContent = choice ? '任意のアクセス解析：' + (choice === 'yes' ? '許可中' : '拒否中') + '。いつでも変更できます。' : explanation;
    accept.hidden = reject.hidden = !!choice;
    edit.hidden = !choice;
  }
  function setChoice(value) {
    choice = value; allowed = value === 'yes';
    try { localStorage.setItem(key, JSON.stringify({ choice: value, time: Date.now() })); } catch (_) {}
    if (allowed) start(); else stop();
    renderChoice();
  }
  accept.addEventListener('click', function () { setChoice('yes'); });
  reject.addEventListener('click', function () { setChoice('no'); });
  edit.addEventListener('click', function () {
    text.textContent = explanation; accept.hidden = reject.hidden = false; edit.hidden = true;
  });
  var header = document.querySelector('header');
  if (header) header.insertAdjacentElement('afterend', panel);
  else (document.querySelector('main') || document.body).prepend(panel);
  renderChoice();
  if (choice) { allowed = choice === 'yes'; if (allowed) start(); else stop(); }
  document.addEventListener('click', function (event) {
    var link = event.target.closest && event.target.closest('a[href]');
    if (!link) return;
    var href = link.getAttribute('href');
    if (/^tel:/.test(href)) report('contact_click', 'phone');
    else if (/^mailto:/.test(href)) report('contact_click', 'email');
    else {
      try {
        var target = new URL(href, window.location.href);
        if (target.origin === window.location.origin && target.hash === '#contact') report('contact_click', 'form');
      } catch (_) {}
    }
  });
  var form = document.querySelector('form[action="https://formspree.io/f/meaazqlq"]');
  if (!form || !window.fetch || !window.FormData) return; // Native POST stays available.
  var began = false;
  form.addEventListener('input', function () {
    if (!began) { began = true; report('form_start'); }
  });
  var status = document.createElement('p');
  status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
  status.hidden = true; form.appendChild(status);
  var button = form.querySelector('button[type="submit"]');
  var nativeButton = document.createElement('button');
  nativeButton.type = 'button'; nativeButton.hidden = true;
  nativeButton.textContent = 'Formspreeの確認画面から送信する';
  nativeButton.addEventListener('click', function () {
    if (!form.reportValidity()) return;
    nativeButton.disabled = true;
    HTMLFormElement.prototype.submit.call(form);
  });
  form.appendChild(nativeButton);
  var submit = core.submissionController(async function (data) {
    var controller = window.AbortController ? new AbortController() : null;
    var timer = controller ? setTimeout(function () { controller.abort(); }, 30000) : null;
    try {
      return await fetch(form.action, { method: 'POST', body: data,
        headers: { Accept: 'application/json' }, signal: controller ? controller.signal : undefined });
    } finally { if (timer) clearTimeout(timer); }
  }, report);
  form.addEventListener('submit', async function (event) {
    event.preventDefault();
    if (!form.reportValidity()) return;
    if (button.disabled) return;
    nativeButton.hidden = true;
    button.disabled = true; status.hidden = false; status.textContent = '送信しています。画面を閉じずにお待ちください。';
    var result = await submit(new FormData(form));
    if (result.state === 'accepted') {
      status.textContent = 'ご相談を受け付けました。担当者が内容を確認後、メールでご連絡します。';
      button.textContent = '受付済み'; form.reset();
    } else if (result.state === 'rejected') {
      status.textContent = '送信は受け付けられませんでした。入力内容を確認してください。改善しない場合は下記の電話・メールでお問い合わせください。';
      button.disabled = false;
      nativeButton.hidden = false;
    } else if (result.state === 'unknown') {
      status.textContent = '受付結果を確認できませんでした。二重送信を避けるため、自動では再送しません。下記の電話・メールから受付状況をご確認ください。入力内容はこの画面に残っています。';
      button.textContent = '受付結果を確認してください';
    }
  });
}());
