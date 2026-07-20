// Ported 1:1 from services/companion/static/shared.js's ICON_ARROW_RIGHT and
// ICON_DIRECTION_SWAP constants (same viewBox/path data, for pixel parity)
// so the Next.js port doesn't invent new glyphs. docs/parity/transport.md #198.

export function ArrowRightIcon() {
  return (
    <svg viewBox="7 3 10 18" fill="currentColor" aria-hidden="true">
      <path
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinejoin="round"
        strokeLinecap="round"
        fillRule="evenodd"
        clipRule="evenodd"
        d="M8.51192 4.43057C8.82641 4.161 9.29989 4.19743 9.56946 4.51192L15.5695 11.5119C15.8102 11.7928 15.8102 12.2072 15.5695 12.4881L9.56946 19.4881C9.29989 19.8026 8.82641 19.839 8.51192 19.5695C8.19743 19.2999 8.161 18.8264 8.43057 18.5119L14.0122 12L8.43057 5.48811C8.161 5.17361 8.19743 4.70014 8.51192 4.43057Z"
      />
    </svg>
  );
}

// --- "Getting there" method icons (docs/getting-there-design.md #3) -------
// Simple line icons keyed by getting-there.json's method `id`. An
// unrecognized id falls back to PinIcon rather than failing to render, so a
// new method never needs an icon before it can show up.

export function TrainIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="5" y="3" width="14" height="13" rx="4" stroke="currentColor" strokeWidth="1.6" />
      <path d="M5 11h14" stroke="currentColor" strokeWidth="1.6" />
      <path d="M8 20l-2 2M16 20l2 2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <circle cx="8.5" cy="13.2" r="0.9" fill="currentColor" />
      <circle cx="15.5" cy="13.2" r="0.9" fill="currentColor" />
    </svg>
  );
}

export function PlaneIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M21 15.5v-2l-8-5V4.5a1.5 1.5 0 0 0-3 0v4l-8 5v2l8-2.5V17l-2.5 1.8V20l3.5-1 3.5 1v-1.2L13 17v-3.5l8 2.5z" />
    </svg>
  );
}

export function CarIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M4.5 16v-2.2l1.7-4.3A2 2 0 0 1 8.1 8.2h7.8a2 2 0 0 1 1.9 1.3l1.7 4.3V16"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <rect x="3.5" y="16" width="17" height="3.4" rx="1.4" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="7.5" cy="19.4" r="1.3" fill="currentColor" />
      <circle cx="16.5" cy="19.4" r="1.3" fill="currentColor" />
    </svg>
  );
}

// "Local transit" (the tram/light-rail board) -- deliberately distinct from
// TrainIcon (used for the long-distance "Train" method) so the two tabs
// don't read as duplicates.
export function TransitIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="4" y="5" width="16" height="11" rx="3" stroke="currentColor" strokeWidth="1.6" />
      <path d="M4 10.5h16" stroke="currentColor" strokeWidth="1.6" />
      <path d="M12 2v3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <circle cx="8.2" cy="19.2" r="1.2" fill="currentColor" />
      <circle cx="15.8" cy="19.2" r="1.2" fill="currentColor" />
    </svg>
  );
}

export function BusIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="4" y="4" width="16" height="12.5" rx="2.2" stroke="currentColor" strokeWidth="1.6" />
      <path d="M4 10.5h16" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="8" cy="19" r="1.3" fill="currentColor" />
      <circle cx="16" cy="19" r="1.3" fill="currentColor" />
    </svg>
  );
}

export function PinIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M12 21s-6.5-6.1-6.5-11A6.5 6.5 0 0 1 18.5 10c0 4.9-6.5 11-6.5 11z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <circle cx="12" cy="10" r="2.2" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}

export function ExternalLinkIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M9 6H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-3M14 4h6v6M20 4l-9.5 9.5"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function ChevronDownIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M6 9l6 6 6-6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function DirectionSwapIcon() {
  return (
    <svg viewBox="5.25 3.25 13.5 17.5" fill="currentColor" aria-hidden="true">
      <path
        fillRule="evenodd"
        clipRule="evenodd"
        d="M10.6634 3.47789C10.9518 3.77526 10.9445 4.25007 10.6471 4.53843L7.8508 7.25H18C18.4142 7.25 18.75 7.58579 18.75 8C18.75 8.41421 18.4142 8.75 18 8.75H7.8508L10.6471 11.4616C10.9445 11.7499 10.9518 12.2247 10.6634 12.5221C10.3751 12.8195 9.90026 12.8268 9.60289 12.5384L5.47789 8.53843C5.33222 8.39717 5.25 8.20291 5.25 8C5.25 7.79709 5.33222 7.60283 5.47789 7.46158L9.60289 3.46158C9.90026 3.17322 10.3751 3.18053 10.6634 3.47789ZM13.3366 11.4779C13.6249 11.1805 14.0997 11.1732 14.3971 11.4616L18.5221 15.4616C18.6678 15.6028 18.75 15.7971 18.75 16C18.75 16.2029 18.6678 16.3972 18.5221 16.5384L14.3971 20.5384C14.0997 20.8268 13.6249 20.8195 13.3366 20.5221C13.0482 20.2247 13.0555 19.7499 13.3529 19.4616L16.1492 16.75L6 16.75C5.58579 16.75 5.25 16.4142 5.25 16C5.25 15.5858 5.58579 15.25 6 15.25L16.1492 15.25L13.3529 12.5384C13.0555 12.2501 13.0482 11.7753 13.3366 11.4779Z"
      />
    </svg>
  );
}
