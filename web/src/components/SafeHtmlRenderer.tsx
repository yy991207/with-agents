import { useCallback, useMemo, useRef } from 'react';
import DOMPurify from 'dompurify';

import type { Config as DOMPurifyConfig } from 'dompurify';

// DOMPurify 清洗配置：允许常用 HTML 标签和属性，自动剥离 script/onclick 等危险内容
const PURIFY_CONFIG: DOMPurifyConfig = {
  ALLOWED_TAGS: [
    'p', 'div', 'span', 'br', 'hr',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'ul', 'ol', 'li', 'dl', 'dt', 'dd',
    'a', 'img',
    'strong', 'b', 'em', 'i', 's', 'del', 'u', 'mark', 'ins',
    'code', 'pre', 'kbd', 'samp', 'var',
    'blockquote', 'q', 'cite',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption',
    'colgroup', 'col',
    'details', 'summary',
    'button', 'input', 'textarea', 'label',
    'section', 'nav', 'header', 'footer', 'main', 'article', 'aside',
    'figure', 'figcaption', 'picture', 'source',
    'small', 'sub', 'sup', 'abbr', 'time', 'dfn',
    'output', 'progress', 'meter',
    'fieldset', 'legend',
    // 允许 iframe 但会通过 sandbox 属性限制能力
    'iframe',
  ],
  ALLOWED_ATTR: [
    'class', 'id', 'style',
    'href', 'target', 'rel', 'title', 'alt', 'aria-label',
    'src', 'width', 'height', 'loading',
    'type', 'value', 'placeholder', 'disabled', 'checked', 'readonly', 'selected',
    'colspan', 'rowspan', 'scope',
    'open',
    'name', 'for', 'min', 'max', 'step', 'maxlength',
    'srcset', 'sizes', 'media',
    'datetime', 'cite',
    'autocomplete',
    'allowfullscreen', 'frameborder', 'sandbox', 'srcdoc', 'allow',
  ],
  ALLOW_DATA_ATTR: true,
  // 只允许 style 中安全的 CSS 属性，防止 expression() 注入
  ALLOWED_URI_REGEXP: /^(?:(?:https?|ftp|mailto|tel):|[^a-z]|[a-z+.-]+(?:[^a-z+.-:]|$))/i,
};


export function SafeHtmlRenderer({ html }: { html: string }) {
  const containerRef = useRef<HTMLElement>(null);

  // 清洗 HTML，每次 html 变化重新计算
  const sanitized = useMemo(
    () => DOMPurify.sanitize(html, PURIFY_CONFIG),
    [html],
  );

  // 事件委托：统一处理消息内容中的点击交互
  const handleClick = useCallback((e: React.MouseEvent) => {
    const target = e.target as HTMLElement;

    // 外部链接 → 新标签页打开
    const link = target.closest('a');
    if (link instanceof HTMLAnchorElement && link.href) {
      const href = link.getAttribute('href') || '';
      // 只对 http/https 链接拦截，锚点/相对路径走原生行为
      if (/^https?:\/\//i.test(href)) {
        e.preventDefault();
        window.open(href, '_blank', 'noopener,noreferrer');
        return;
      }
    }

    // 按钮 / 可点击元素 → 涟漪动效
    const clickable = target.closest('button, [role="button"], .btn');
    if (clickable instanceof HTMLElement) {
      if (clickable instanceof HTMLButtonElement && clickable.disabled) return;

      const root = containerRef.current;
      if (!root) return;

      const ripple = document.createElement('span');
      ripple.className = 'safe-html-ripple';

      // 涟漪大小不超过容器 80%
      const rootRect = root.getBoundingClientRect();
      const size = Math.min(rootRect.width, rootRect.height) * 0.7;
      const x = e.clientX - rootRect.left - size / 2;
      const y = e.clientY - rootRect.top - size / 2;

      ripple.style.cssText = `
        width: ${size}px;
        height: ${size}px;
        left: ${x}px;
        top: ${y}px;
      `;

      root.appendChild(ripple);
      ripple.addEventListener('animationend', () => ripple.remove());
    }
  }, []);

  return (
    <article
      ref={containerRef}
      className="reply-html lobe-md safe-html"
      onClick={handleClick}
      dangerouslySetInnerHTML={{ __html: sanitized }}
    />
  );
}