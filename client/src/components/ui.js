import React from 'react';

// One meaning per colour, so a glance tells what a pill is asking for:
//   crew       ink, filled   a person's call (crew alerts): the only filled pill, never an AI label
//   urgent     red           act now: the AI says Prepare now, or processing failed
//   attention  amber         someone needs to look: not acknowledged, needs review, Cannot assess
//   neutral    gray          normal status
//   info       blue          new, or in progress
const TONES = {
  crew: 'bg-slate-900 text-white border-slate-900',
  urgent: 'bg-white text-red-700 border-red-300',
  attention: 'bg-amber-50 text-amber-900 border-amber-300',
  neutral: 'bg-gray-100 text-gray-700 border-gray-200',
  info: 'bg-blue-50 text-blue-800 border-blue-200',
};

export const Pill = ({ tone = 'neutral', bold = false, pulse = false, className = '', children, ...rest }) => (
  <span
    className={`inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full border ${bold ? 'font-bold' : 'font-medium'} ${TONES[tone]} ${pulse ? 'animate-pulse' : ''} ${className}`}
    {...rest}
  >
    {children}
  </span>
);

// The heading of one block of a case (AI handoff, Vitals, Transcript, ...).
export const SectionLabel = ({ as: Tag = 'h4', className = '', children }) => (
  <Tag className={`text-sm font-semibold text-gray-900 ${className}`}>{children}</Tag>
);
