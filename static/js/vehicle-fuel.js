(() => {
  // Match the server's Decimal ROUND_HALF_UP before multiplying the fields.
  const scaled = (value, digits) => {
    const text = value.replace(/\s/g, '').replace(',', '.');
    if (text.length > 64 || !/^\+?(?:\d+(?:\.\d*)?|\.\d+)$/.test(text)) return null;
    const [whole, fraction = ''] = text.replace(/^\+/, '').split('.');
    const scale = 10n ** BigInt(digits);
    const units = BigInt(whole || '0') * scale + BigInt((fraction + '0'.repeat(digits)).slice(0, digits));
    return units + (fraction[digits] >= '5' ? 1n : 0n);
  };
  document.querySelectorAll('.vehicle-fuel-form').forEach(form => {
    const liters = form.elements.liters, price = form.elements.price_per_liter;
    const calculate = () => {
      const volume = scaled(liters.value, 3), unitPrice = scaled(price.value, 2);
      if (volume === null || unitPrice === null || volume <= 0n || unitPrice <= 0n) return;
      const cents = (volume * unitPrice + 500n) / 1000n;
      form.elements.total_amount.value = String(cents / 100n) + '.' + String(cents % 100n).padStart(2, '0');
    };
    liters.addEventListener('input', calculate);
    price.addEventListener('input', calculate);
    form.addEventListener('submit', () => {
      form.querySelector('[data-fuel-unchecked]').disabled = form.querySelector('#fuelFullTank').checked;
    });
  });
})();
