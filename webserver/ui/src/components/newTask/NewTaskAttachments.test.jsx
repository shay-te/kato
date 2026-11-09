import { describe, expect, test, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import NewTaskAttachments from './NewTaskAttachments.jsx';
import { MAX_ATTACHMENT_BYTES } from './newTaskHelpers.js';

function textFile(name, content = 'hello') {
  return new File([content], name, { type: 'text/plain' });
}

describe('NewTaskAttachments', () => {
  test('nothing attached renders no list, just the hint and the button', () => {
    render(<NewTaskAttachments files={[]} onChange={vi.fn()} />);
    expect(screen.queryByRole('list')).toBeNull();
    expect(screen.getByRole('button', { name: /Attach files/ })).toBeInTheDocument();
    expect(screen.getByText(/a spec, a CSV, a PDF/)).toBeInTheDocument();
  });

  test('picking a file from the input appends it', () => {
    const onChange = vi.fn();
    render(<NewTaskAttachments files={[]} onChange={onChange} />);
    const input = screen.getByLabelText('Attach files');
    fireEvent.change(input, { target: { files: [textFile('spec.pdf')] } });
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0][0].map((f) => f.name)).toEqual(['spec.pdf']);
  });

  test('a file over the size cap is not appended', () => {
    const onChange = vi.fn();
    const huge = textFile('huge.csv');
    Object.defineProperty(huge, 'size', { value: MAX_ATTACHMENT_BYTES + 1 });
    render(<NewTaskAttachments files={[]} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Attach files'), { target: { files: [huge] } });
    expect(onChange).toHaveBeenCalledWith([]);
  });

  test('every attached file is listed with a remove button', () => {
    const files = [textFile('a.csv'), textFile('b.pdf')];
    render(<NewTaskAttachments files={files} onChange={vi.fn()} />);
    expect(screen.getByText('a.csv')).toBeInTheDocument();
    expect(screen.getByText('b.pdf')).toBeInTheDocument();
    expect(screen.getByText('2 file(s)')).toBeInTheDocument();
  });

  test('removing one file keeps the others, by index not identity', () => {
    const files = [textFile('a.csv'), textFile('b.pdf'), textFile('a.csv')];
    const onChange = vi.fn();
    render(<NewTaskAttachments files={files} onChange={onChange} />);
    fireEvent.click(screen.getAllByTitle('Remove')[1]);
    expect(onChange.mock.calls[0][0].map((f) => f.name)).toEqual(['a.csv', 'a.csv']);
  });

  test('dropping files appends them and clears the drop-target state', () => {
    const onChange = vi.fn();
    render(<NewTaskAttachments files={[]} onChange={onChange} />);
    const section = screen.getByText('Attachments').closest('section');
    fireEvent.dragEnter(section, { dataTransfer: { types: ['Files'] } });
    expect(section).toHaveClass('is-drop-target');
    fireEvent.drop(section, { dataTransfer: { files: [textFile('dropped.xlsx')], types: ['Files'] } });
    expect(onChange.mock.calls[0][0].map((f) => f.name)).toEqual(['dropped.xlsx']);
    expect(section).not.toHaveClass('is-drop-target');
  });

  test('a drag carrying no files is ignored', () => {
    render(<NewTaskAttachments files={[]} onChange={vi.fn()} />);
    const section = screen.getByText('Attachments').closest('section');
    fireEvent.dragEnter(section, { dataTransfer: { types: ['text/plain'] } });
    expect(section).not.toHaveClass('is-drop-target');
  });
});
