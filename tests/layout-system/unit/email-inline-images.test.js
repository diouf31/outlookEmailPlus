/**
 * 单元测试 - 邮件详情内联图片重写
 *
 * 覆盖：
 * - cid: 引用按归一化 key 映射到 inline_resources
 * - 非 cid 外链图片不受影响
 * - renderEmailDetail 经过重写后仍把 data:image 内容传给 iframe
 */

const fs = require('fs');
const path = require('path');

describe('邮件详情内联图片重写', () => {
  let emailApi;

  beforeAll(() => {
    const scriptPath = path.resolve(__dirname, '../../../static/js/features/emails.js');
    const script = fs.readFileSync(scriptPath, 'utf8');
    window.eval(`${script}
window.__emailInlineImageTestApi = {
  normalizeEmailInlineResourceKey,
  resolveEmailInlineResource,
  rewriteEmailInlineImages,
  rewriteEmailLinksForExternalOpen,
  isExternalEmailNavigationHref,
  bindEmailLinksOpenInBrowser,
  renderEmailDetail
};`);
    emailApi = window.__emailInlineImageTestApi;
  });

  beforeEach(() => {
    document.body.innerHTML = `
      <div id="emailDetail"></div>
      <div id="emailDetailToolbar"></div>
    `;

    global.escapeHtml = (value) => String(value ?? '');
    global.formatDate = (value) => String(value ?? '');
    global.adjustIframeHeight = jest.fn();
    global.isTrustedMode = false;
    global.isTempEmailGroup = false;
    global.currentPage = 'mailbox';
    global.DOMPurify = {
      sanitize: jest.fn((html) => html)
    };

    window.escapeHtml = global.escapeHtml;
    window.formatDate = global.formatDate;
    window.adjustIframeHeight = global.adjustIframeHeight;
    window.isTrustedMode = global.isTrustedMode;
    window.isTempEmailGroup = global.isTempEmailGroup;
    window.currentPage = global.currentPage;
    window.DOMPurify = global.DOMPurify;
  });

  test('rewriteEmailInlineImages rewrites cid sources with normalized keys', () => {
    const html = '<div><img src="cid:<captcha-1>" alt="captcha"></div>';
    const email = {
      inline_resources: {
        'captcha-1': 'data:image/png;base64,QUJDRA=='
      }
    };

    const result = emailApi.rewriteEmailInlineImages(html, email);

    expect(result).toContain('data:image/png;base64,QUJDRA==');
    expect(result).not.toContain('cid:<captcha-1>');
  });

  test('rewriteEmailInlineImages preserves non-cid images and unresolved cid references', () => {
    const html = [
      '<div>',
      '<img src="https://cdn.example.com/captcha.png" alt="remote">',
      '<img src="cid:missing-inline" alt="missing">',
      '</div>'
    ].join('');

    const result = emailApi.rewriteEmailInlineImages(html, {
      inline_resources: {
        'other-inline': 'data:image/png;base64,AAAA'
      }
    });

    expect(result).toContain('https://cdn.example.com/captcha.png');
    expect(result).toContain('cid:missing-inline');
  });

  test('renderEmailDetail passes rewritten inline image html into iframe srcdoc', () => {
    emailApi.renderEmailDetail({
      subject: 'Inline Image',
      from: 'sender@example.com',
      to: 'reader@example.com',
      cc: '',
      date: '2026-03-22T12:00:00',
      body_type: 'html',
      body: '<div><img src="cid:captcha-2" alt="captcha"></div>',
      inline_resources: {
        'captcha-2': 'data:image/png;base64,QUJDRA=='
      }
    });

    const iframe = document.getElementById('emailBodyFrame');

    expect(global.DOMPurify.sanitize).toHaveBeenCalled();
    expect(iframe).not.toBeNull();
    expect(iframe.srcdoc).toContain('data:image/png;base64,QUJDRA==');
    expect(iframe.srcdoc).not.toContain('cid:captcha-2');
  });

  test('rewriteEmailLinksForExternalOpen forces http(s) links to open externally', () => {
    const html = [
      '<div>',
      '<a href="https://click.salesforce.com/confirm">Confirm Email Address</a>',
      '<a href="#section">anchor</a>',
      '<a href="javascript:void(0)">noop</a>',
      '</div>'
    ].join('');

    const result = emailApi.rewriteEmailLinksForExternalOpen(html);

    expect(result).toContain('href="https://click.salesforce.com/confirm"');
    expect(result).toContain('target="_blank"');
    expect(result).toContain('rel="noopener noreferrer"');
    expect(result).toContain('href="#section"');
    expect(result).not.toMatch(/href="#section"[^>]*target="_blank"/);
  });

  test('renderEmailDetail srcdoc includes base target blank and popup sandbox', () => {
    emailApi.renderEmailDetail({
      subject: 'Pirelli Survey - Complete Registration',
      from: 'noreply@pirelli.com',
      to: 'user@outlook.com',
      date: '2026-11-08T14:43:00',
      body_type: 'html',
      body: '<a href="https://click.salesforce.com/confirm">Confirm Email Address</a>'
    });

    const iframe = document.getElementById('emailBodyFrame');
    expect(iframe).not.toBeNull();
    expect(iframe.getAttribute('sandbox')).toContain('allow-popups');
    expect(iframe.srcdoc).toContain('<base target="_blank">');
    expect(iframe.srcdoc).toContain('https://click.salesforce.com/confirm');
    expect(iframe.srcdoc).toContain('target="_blank"');
  });

  test('bindEmailLinksOpenInBrowser opens http links via window.open', () => {
    const iframe = document.createElement('iframe');
    document.body.appendChild(iframe);
    iframe.contentDocument.open();
    iframe.contentDocument.write('<a id="cta" href="https://click.salesforce.com/confirm">Confirm</a>');
    iframe.contentDocument.close();

    const openSpy = jest.spyOn(window, 'open').mockImplementation(() => null);
    emailApi.bindEmailLinksOpenInBrowser(iframe);

    const anchor = iframe.contentDocument.getElementById('cta');
    const event = iframe.contentDocument.createEvent('MouseEvents');
    event.initEvent('click', true, true);
    anchor.dispatchEvent(event);

    expect(openSpy).toHaveBeenCalled();
    expect(String(openSpy.mock.calls[0][0])).toContain('https://click.salesforce.com/confirm');
    openSpy.mockRestore();
  });
});
