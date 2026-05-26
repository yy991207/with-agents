import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
  type HTMLAttributes,
  type ReactNode,
  type WheelEvent,
  type TouchEvent,
} from 'react';

export interface TransientScrollbarProps extends HTMLAttributes<HTMLDivElement> {
  visibleMs?: number;
  followResetKey?: unknown;
  followThresholdPx?: number;
  resetPosition?: 'top' | 'bottom';
}

function buildClassName(baseClassName?: string, active?: boolean): string {
  return ['transient-scrollbar', active ? 'is-scrolling' : '', baseClassName ?? '']
    .filter(Boolean)
    .join(' ');
}

const TransientScrollbar = forwardRef<HTMLDivElement, TransientScrollbarProps>(
  function TransientScrollbar(
    {
      className,
      children,
      onTouchMove,
      onWheel,
      onScroll,
      followResetKey,
      followThresholdPx = 8,
      resetPosition = 'bottom',
      visibleMs = 720,
      ...rest
    },
    ref,
  ) {
    const [scrolling, setScrolling] = useState(false);
    const [followBottom, setFollowBottom] = useState(true);
    const followBottomRef = useRef(true);
    const hideTimerRef = useRef<number | null>(null);
    const outerRef = useRef<HTMLDivElement | null>(null);
    const contentRef = useRef<HTMLDivElement | null>(null);
    const rafRef = useRef<number | null>(null);

    useImperativeHandle(ref, () => outerRef.current as HTMLDivElement, []);

    const stickToBottom = useCallback(() => {
      const el = outerRef.current;
      if (!el) return;
      el.scrollTop = el.scrollHeight;
    }, []);

    const stickToTop = useCallback(() => {
      const el = outerRef.current;
      if (!el) return;
      el.scrollTop = 0;
    }, []);

    const scheduleResetPosition = useCallback(() => {
      if (rafRef.current !== null) {
        window.cancelAnimationFrame(rafRef.current);
      }
      rafRef.current = window.requestAnimationFrame(() => {
        rafRef.current = null;
        if (resetPosition === 'top') {
          stickToTop();
          return;
        }
        stickToBottom();
      });
    }, [resetPosition, stickToBottom, stickToTop]);

    const markScrolling = useCallback(() => {
      setScrolling(true);
      if (hideTimerRef.current !== null) {
        window.clearTimeout(hideTimerRef.current);
      }
      hideTimerRef.current = window.setTimeout(() => {
        setScrolling(false);
        hideTimerRef.current = null;
      }, visibleMs);
    }, [visibleMs]);

    useLayoutEffect(() => {
      const nextFollowBottom = resetPosition === 'bottom';
      setFollowBottom(nextFollowBottom);
      followBottomRef.current = nextFollowBottom;
      scheduleResetPosition();
    }, [followResetKey, resetPosition, scheduleResetPosition]);

    useEffect(() => {
      followBottomRef.current = followBottom;
    }, [followBottom]);

    useEffect(() => {
      return () => {
        if (hideTimerRef.current !== null) {
          window.clearTimeout(hideTimerRef.current);
        }
        if (rafRef.current !== null) {
          window.cancelAnimationFrame(rafRef.current);
        }
      };
    }, []);

    useEffect(() => {
      const contentEl = contentRef.current;
      if (!contentEl || typeof ResizeObserver === 'undefined') return;
      const observer = new ResizeObserver(() => {
        if (!followBottomRef.current) return;
        scheduleResetPosition();
      });
      observer.observe(contentEl);
      return () => observer.disconnect();
    }, [scheduleResetPosition]);

    const handleScroll = useCallback(
      (event: React.UIEvent<HTMLDivElement>) => {
        onScroll?.(event);
        const el = event.currentTarget;
        const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= followThresholdPx;
        setFollowBottom(atBottom);
        followBottomRef.current = atBottom;
      },
      [followThresholdPx, onScroll],
    );

    const handleWheel = useCallback(
      (event: WheelEvent<HTMLDivElement>) => {
        onWheel?.(event);
        markScrolling();
      },
      [markScrolling, onWheel],
    );

    const handleTouchMove = useCallback(
      (event: TouchEvent<HTMLDivElement>) => {
        onTouchMove?.(event);
        markScrolling();
      },
      [markScrolling, onTouchMove],
    );

    return (
      <div
        {...rest}
        ref={outerRef}
        className={buildClassName(className, scrolling)}
        onScroll={handleScroll}
        onTouchMove={handleTouchMove}
        onWheel={handleWheel}
      >
        <div ref={contentRef}>{children as ReactNode}</div>
      </div>
    );
  },
);

TransientScrollbar.displayName = 'TransientScrollbar';

export default TransientScrollbar;
