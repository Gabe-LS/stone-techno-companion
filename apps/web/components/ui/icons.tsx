// Shared icons for apps/web/components/ui/* primitives. Kept separate from
// components/transport/icons.tsx (page-specific glyphs) — anything a UI
// primitive itself renders (not a page composing icons around a primitive)
// belongs here.

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
