import React from 'react';

// One look for the whole app. client/src/designRules.test.js keeps these from eroding.
//   type     system sans only. Sizes: 12 caption, 14 body, 16 reading text, 20 titles. Weights 400, 500, 600.
//   colour   neutral grays; blue is the one accent (actions, links, selection); red only for urgent;
//            amber only as text or a dot; the dark chip is reserved for a crew's alert
//   icons    lucide, 16px; no emoji
//   quiet    a normal state gets no badge: only exceptions are marked

const TONES = {
  urgent: { text: 'text-red-600', dot: 'bg-red-500' },
  attention: { text: 'text-amber-700', dot: 'bg-amber-500' },
  info: { text: 'text-blue-600', dot: 'bg-blue-500' },
  ok: { text: 'text-green-700', dot: 'bg-green-500' },
  neutral: { text: 'text-gray-600', dot: 'bg-gray-400' },
};

/** A state worth a glance: coloured text with a small dot. Never a filled or outlined pill. */
export const StatusText = ({ tone = 'neutral', dot = true, pulse = false, className = '', children, ...rest }) => (
  <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${TONES[tone].text} ${className}`} {...rest}>
    {dot && <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${TONES[tone].dot} ${pulse ? 'animate-pulse' : ''}`} />}
    {children}
  </span>
);

/** A crew's call: the only filled chip, so it is never mistaken for an AI label. */
export const CrewChip = ({ className = '', children, ...rest }) => (
  <span className={`inline-flex items-center rounded-md bg-slate-900 px-2 py-0.5 text-xs font-medium text-white ${className}`} {...rest}>
    {children}
  </span>
);

const BUTTONS = {
  primary: 'bg-blue-600 text-white hover:bg-blue-700',
  secondary: 'border border-gray-300 bg-white text-gray-700 hover:bg-gray-50',
  ghost: 'text-gray-600 hover:bg-gray-100 hover:text-gray-900',
};

export const Button = ({ variant = 'secondary', type = 'button', className = '', ...rest }) => (
  <button type={type}
    className={`inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-50 ${BUTTONS[variant]} ${className}`}
    {...rest} />
);

// The heading of one block of a screen (AI summary, Vitals, Transcript, ...).
export const SectionLabel = ({ as: Tag = 'h4', className = '', children }) => (
  <Tag className={`text-sm font-semibold text-gray-900 ${className}`}>{children}</Tag>
);

export const Caption = ({ as: Tag = 'p', className = '', children }) => (
  <Tag className={`text-xs text-gray-500 ${className}`}>{children}</Tag>
);
