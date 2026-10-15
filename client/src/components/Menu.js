import React, { useEffect, useRef, useState } from 'react';
import { Check, ChevronDown } from 'lucide-react';

/**
 * A dropdown that picks one of a few options, with a line under each saying what it means. It
 * replaces native selects and rows of pill buttons. Arrow keys move between options, Escape and a
 * click elsewhere close it. options: [{ value, label, description }].
 */
const Menu = ({ label, value, options, onChange, disabled = false, align = 'right', footer = null }) => {
  const [open, setOpen] = useState(false);
  const root = useRef(null);
  const trigger = useRef(null);
  const selected = options.find((option) => option.value === value) || options[0];
  const optionButtons = () => [...root.current.querySelectorAll('[role="option"]')];

  useEffect(() => {
    if (!open) return undefined;
    const closeOnOutside = (event) => { if (!root.current?.contains(event.target)) setOpen(false); };
    const closeOnEscape = (event) => {
      if (event.key === 'Escape') { setOpen(false); trigger.current?.focus(); }
    };
    document.addEventListener('mousedown', closeOnOutside);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('mousedown', closeOnOutside);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [open]);

  useEffect(() => {
    if (open) root.current.querySelector('[aria-selected="true"]')?.focus();
  }, [open]);

  const onOptionKey = (event) => {
    const buttons = optionButtons();
    const at = buttons.indexOf(document.activeElement);
    const next = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: buttons.length - 1 }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    buttons[(next + buttons.length) % buttons.length].focus();
  };

  const choose = (option) => {
    setOpen(false);
    trigger.current?.focus();
    if (option.value !== value) onChange(option.value);
  };

  return (
    <div ref={root} className="relative">
      <button ref={trigger} type="button" disabled={disabled} aria-haspopup="listbox" aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={(event) => { if (event.key === 'ArrowDown' && !open) { event.preventDefault(); setOpen(true); } }}
        className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-sm text-gray-600 hover:bg-gray-100 hover:text-gray-900 disabled:opacity-60">
        {label}: <span className="font-medium text-gray-900">{selected.label}</span>
        <ChevronDown className="h-4 w-4 text-gray-500" />
      </button>
      {open && (
        <div className={`absolute top-full z-20 mt-1 w-80 max-w-[90vw] rounded-lg border border-gray-200 bg-white p-1 shadow-lg ${align === 'right' ? 'right-0' : 'left-0'}`}>
          <div role="listbox" aria-label={label} onKeyDown={onOptionKey}>
            {options.map((option) => (
              <button key={option.value} type="button" role="option" aria-selected={option.value === value}
                onClick={() => choose(option)}
                className="flex w-full items-start gap-2 rounded-md px-3 py-2 text-left hover:bg-gray-50 focus:bg-gray-50 focus:outline-none">
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium text-gray-900">{option.label}</span>
                  {option.description && <span className="block text-xs text-gray-500">{option.description}</span>}
                </span>
                {option.value === value && <Check className="mt-0.5 h-4 w-4 shrink-0 text-blue-600" />}
              </button>
            ))}
          </div>
          {footer && <div className="border-t border-gray-100 px-3 py-2 text-xs text-gray-500">{footer}</div>}
        </div>
      )}
    </div>
  );
};

export default Menu;
