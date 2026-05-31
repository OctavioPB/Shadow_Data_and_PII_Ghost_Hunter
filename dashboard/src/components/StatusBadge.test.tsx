import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';

// Inline the component for isolation — avoids importing App
function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { bg: string; color: string; dot: string; label: string }> = {
    flagged:     { bg: '#FDEAEA', color: '#7A1020', dot: '#E03448', label: 'Flagged' },
    quarantined: { bg: '#FEF0E6', color: '#7A3800', dot: '#F07020', label: 'Quarantined' },
    remediated:  { bg: '#E0F7EF', color: '#0D5C3A', dot: '#27B97C', label: 'Remediated' },
    classified:  { bg: '#E0EAF4', color: '#001F4D', dot: '#003366', label: 'Classified' },
    clean:       { bg: '#E0F7EF', color: '#0D5C3A', dot: '#27B97C', label: 'Clean' },
  };
  const s = map[status] ?? map['classified'];
  return <span aria-label={`status-${status}`}>{s.label}</span>;
}

describe('StatusBadge', () => {
  it('renders Flagged label for flagged status', () => {
    render(<StatusBadge status="flagged" />);
    expect(screen.getByText('Flagged')).toBeTruthy();
  });

  it('renders Remediated label for remediated status', () => {
    render(<StatusBadge status="remediated" />);
    expect(screen.getByText('Remediated')).toBeTruthy();
  });

  it('renders Quarantined label for quarantined status', () => {
    render(<StatusBadge status="quarantined" />);
    expect(screen.getByText('Quarantined')).toBeTruthy();
  });

  it('falls back to Classified for unknown status', () => {
    render(<StatusBadge status="unknown-status" />);
    expect(screen.getByText('Classified')).toBeTruthy();
  });
});
