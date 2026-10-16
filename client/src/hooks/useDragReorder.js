import { useEffect, useRef, useState } from 'react';

/**
 * Reorder a list by dragging each row's handle, with a mouse, a finger or a pen (pointer events, so it
 * works on a phone), or with the keyboard: on a focused handle, the up and down arrows move the row one
 * place. ids: the rows in their current order. onReorder(id, to) is called once, when the row is dropped:
 * the row's id (the list may change while it is held) and the index it should end up at.
 * Spread rowProps(id, index) on each row and handleProps(id, index) on its handle. A held row is let go,
 * without saving, if the rows change order or leave under it, or if reordering is turned off (enabled).
 */
const useDragReorder = (ids, onReorder, enabled = true) => {
  const [drag, setDrag] = useState(null);   // { from, over, dy, height } while a row is held
  const rows = useRef({});
  const handles = useRef({});
  const start = useRef(null);
  const refocus = useRef(null);

  const order = ids.join(',');
  // A row moved with the keyboard keeps its handle focused, wherever the list puts it.
  useEffect(() => {
    if (refocus.current) handles.current[refocus.current]?.focus();
    refocus.current = null;
  }, [order]);
  // The positions measured when the row was picked up no longer describe the list: let it go.
  useEffect(() => setDrag(null), [order, enabled]);

  const onPointerDown = (index) => (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    const rects = ids.map((id) => rows.current[id].getBoundingClientRect());
    start.current = { y: event.clientY, rects, id: ids[index] };
    setDrag({ from: index, over: index, dy: 0, height: rects[index].height });
  };

  const onPointerMove = (event) => {
    if (!drag) return;
    const { y, rects } = start.current;
    const dy = event.clientY - y;
    const held = rects[drag.from];
    const middle = held.top + held.height / 2 + dy;
    // Where it would land: after every other row whose middle it has passed.
    const over = rects.filter((rect, index) => index !== drag.from && rect.top + rect.height / 2 < middle).length;
    setDrag((current) => current && { ...current, dy, over });
  };

  const onPointerUp = () => {
    if (drag && drag.over !== drag.from && ids[drag.from] === start.current.id) onReorder(start.current.id, drag.over);
    setDrag(null);
  };

  const onKeyDown = (id, index) => (event) => {
    const step = { ArrowUp: -1, ArrowDown: 1 }[event.key];
    if (!step || index + step < 0 || index + step >= ids.length) return;
    event.preventDefault();
    refocus.current = id;
    onReorder(id, index + step);
  };

  // While a row is held it follows the pointer, and the rows between where it was and where it will
  // land slide over by its height to make room.
  const offset = (index) => {
    if (!drag) return 0;
    if (index === drag.from) return drag.dy;
    if (drag.from < drag.over && index > drag.from && index <= drag.over) return -drag.height;
    if (drag.over < drag.from && index >= drag.over && index < drag.from) return drag.height;
    return 0;
  };

  const rowProps = (id, index) => {
    const held = drag?.from === index;
    return {
      ref: (element) => { rows.current[id] = element; },
      style: drag ? {
        transform: `translateY(${offset(index)}px)`,
        transition: held ? 'none' : 'transform 150ms ease',
        position: 'relative',
        zIndex: held ? 10 : undefined,
      } : undefined,
      'data-dragging': held || undefined,
    };
  };

  const handleProps = (id, index) => ({
    ref: (element) => { handles.current[id] = element; },
    onPointerDown: onPointerDown(index),
    onPointerMove,
    onPointerUp,
    onPointerCancel: () => setDrag(null),
    onLostPointerCapture: () => setDrag(null),
    onKeyDown: onKeyDown(id, index),
  });

  return { rowProps, handleProps };
};

export default useDragReorder;
