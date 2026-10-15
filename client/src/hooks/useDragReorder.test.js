import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import useDragReorder from './useDragReorder';

let container;
let root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => { act(() => root.unmount()); container.remove(); });

// Three 50 px rows, stacked from the top of the page.
const List = ({ ids, onReorder }) => {
  const { rowProps, handleProps } = useDragReorder(ids, onReorder);
  return (
    <ul>
      {ids.map((id, index) => {
        const { ref, ...rest } = rowProps(id, index);
        const measured = (element) => {
          if (element) element.getBoundingClientRect = () => ({ top: index * 50, height: 50 });
          ref(element);
        };
        return <li key={id} ref={measured} {...rest}><button type="button" aria-label={`Move ${id}`} {...handleProps(id, index)} /></li>;
      })}
    </ul>
  );
};
const pointer = (type, target, clientY) => {
  const event = new MouseEvent(type, { bubbles: true, clientY, button: 0 });
  act(() => target.dispatchEvent(event));
};
const handle = (id) => container.querySelector(`[aria-label="Move ${id}"]`);

test('dragging a row past the middle of others drops it there, once', () => {
  const onReorder = jest.fn();
  act(() => root.render(<List ids={['a', 'b', 'c']} onReorder={onReorder} />));
  pointer('pointerdown', handle('a'), 25);
  pointer('pointermove', handle('a'), 110);    // its middle (25 + 85) is past b's (75) but not c's (125)
  expect(container.querySelectorAll('li')[1].style.transform).toBe('translateY(-50px)');
  pointer('pointerup', handle('a'), 110);
  expect(onReorder).toHaveBeenCalledTimes(1);
  expect(onReorder).toHaveBeenCalledWith('a', 1);
  expect(container.querySelector('li').style.transform).toBe('');
});

test('a drop where it started saves nothing', () => {
  const onReorder = jest.fn();
  act(() => root.render(<List ids={['a', 'b', 'c']} onReorder={onReorder} />));
  pointer('pointerdown', handle('b'), 75);
  pointer('pointermove', handle('b'), 85);
  pointer('pointerup', handle('b'), 85);
  expect(onReorder).not.toHaveBeenCalled();
});

test('the arrow keys move a row one place, never past either end', () => {
  const onReorder = jest.fn();
  act(() => root.render(<List ids={['a', 'b', 'c']} onReorder={onReorder} />));
  const key = (id, k) => act(() => handle(id).dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true })));
  key('a', 'ArrowUp');
  key('c', 'ArrowDown');
  expect(onReorder).not.toHaveBeenCalled();
  key('b', 'ArrowUp');
  expect(onReorder).toHaveBeenCalledWith('b', 0);
});
