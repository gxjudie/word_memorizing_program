import { useCallback, useEffect, useRef, type ReactNode } from 'react'

/** Elements that can receive focus inside a dialog, in tab order. */
const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

export interface DialogProps {
  /** Id of the element that names the dialog (its heading). */
  labelledBy: string
  /** Id of the element that describes the dialog, if any. */
  describedBy?: string
  /** Close handler; called on Escape, backdrop click, and from the content. */
  onClose: () => void
  /** Panel content. The dialog supplies the surface, focus trap, and scroll lock. */
  children: ReactNode
  /** Overrides for the panel (width, padding, radius). */
  className?: string
  testId?: string
}

/**
 * Accessible modal dialog.
 *
 * The app needs two of these (the Gemini key guide and the word editor), so the
 * shared behaviour lives here: `role="dialog"` + `aria-modal`, initial focus,
 * a Tab/Shift+Tab focus trap, Escape to close, background scroll lock, and
 * focus restored to the trigger on unmount. Neither dialog is rendered while
 * closed, so mounting is the open transition.
 */
export function Dialog({
  labelledBy,
  describedBy,
  onClose,
  children,
  className = '',
  testId,
}: DialogProps) {
  const panelRef = useRef<HTMLDivElement>(null)

  const focusables = useCallback((): HTMLElement[] => {
    const panel = panelRef.current
    if (!panel) return []
    return Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE))
  }, [])

  useEffect(() => {
    const previouslyFocused = document.activeElement
    const panel = panelRef.current
    const first = focusables()[0]
    ;(first ?? panel)?.focus()

    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        onClose()
        return
      }
      if (event.key !== 'Tab') return

      const items = focusables()
      if (items.length === 0) {
        event.preventDefault()
        panel?.focus()
        return
      }
      const firstItem = items[0]
      const lastItem = items[items.length - 1]
      const active = document.activeElement

      if (event.shiftKey && (active === firstItem || active === panel)) {
        event.preventDefault()
        lastItem.focus()
      } else if (!event.shiftKey && active === lastItem) {
        event.preventDefault()
        firstItem.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown, true)
    return () => {
      document.removeEventListener('keydown', handleKeyDown, true)
      document.body.style.overflow = previousOverflow
      if (previouslyFocused instanceof HTMLElement) previouslyFocused.focus()
    }
  }, [focusables, onClose])

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-ink/40 px-4 py-6 backdrop-blur-sm sm:py-12"
      onMouseDown={(event) => {
        // Only a press on the backdrop itself closes the dialog, so a drag that
        // starts inside the panel and ends outside does not dismiss it.
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        aria-describedby={describedBy}
        tabIndex={-1}
        data-testid={testId}
        className={
          'w-full max-w-2xl rounded-[2rem] bg-washi p-5 shadow-card outline-none sm:p-7 ' +
          className
        }
      >
        {children}
      </div>
    </div>
  )
}

export default Dialog
