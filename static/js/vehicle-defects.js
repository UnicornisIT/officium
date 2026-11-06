document.querySelectorAll('[data-defect-status-hint]').forEach(hint => {
  const severity = hint.closest('form').elements.severity;
  const sync = () => {
    hint.hidden = severity.value === 'critical';
  };
  severity.addEventListener('change', sync);
  sync();
});
