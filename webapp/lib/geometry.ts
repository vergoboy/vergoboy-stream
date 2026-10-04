/**
 * Pointer maths that still works when the player is turned 90° by the CSS
 * "forced landscape" fallback (phones that refuse to lock orientation).
 *
 * The wrapper is rotated clockwise about its top-left corner and shifted right
 * by one viewport width, so local x runs down the screen and local y runs
 * leftwards. These helpers convert a pointer position into the element's own
 * coordinate space either way.
 */
export function localPoint(e: { clientX: number; clientY: number }, el: HTMLElement, rotated: boolean) {
  const r = el.getBoundingClientRect();
  if (!rotated) return { x: e.clientX - r.left, y: e.clientY - r.top, w: r.width, h: r.height };
  return { x: e.clientY - r.top, y: r.right - e.clientX, w: r.height, h: r.width };
}

export function ratioAlong(e: { clientX: number; clientY: number }, el: HTMLElement, rotated: boolean) {
  const p = localPoint(e, el, rotated);
  return Math.min(1, Math.max(0, p.w ? p.x / p.w : 0));
}
