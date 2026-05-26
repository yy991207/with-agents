import {
  forwardRef,
  useCallback,
  useEffect,
  useRef,
  useState,
  type HTMLAttributes,
  type WheelEvent,
  type TouchEvent,
} from 'react';

export interface TransientScrollbarProps extends HTMLAttributes<HTMLDivElement> {
  visibleMs?: number;
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
      onTouchMove,
      onWheel,
      visibleMs = 720,
      ...rest
    },
    ref,
  ) {
    const [scrolling, setScrolling] = useState(false);
    const hideTimerRef = useRef<number | null>(null);

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

    useEffect(() => {
      return () => {
        if (hideTimerRef.current !== null) {
          window.clearTimeout(hideTimerRef.current);
        }
      };
    }, []);

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
        ref={ref}
        className={buildClassName(className, scrolling)}
        onTouchMove={handleTouchMove}
        onWheel={handleWheel}
      />
    );
  },
);

TransientScrollbar.displayName = 'TransientScrollbar';

export default TransientScrollbar;
