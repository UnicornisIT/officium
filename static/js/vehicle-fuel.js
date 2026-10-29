(() => {
  document.querySelectorAll('.vehicle-fuel-form').forEach(form => {
    const liters = form.elements.liters, price = form.elements.price_per_liter;
    const number = value => Number(value.replace(/\s/g, '').replace(',', '.'));
    const calculate = () => {
      const result = number(liters.value) * number(price.value);
      if (Number.isFinite(result) && result > 0) form.elements.total_amount.value = result.toFixed(2);
    };
    liters.addEventListener('input', calculate);
    price.addEventListener('input', calculate);
    form.addEventListener('submit', () => {
      form.querySelector('[data-fuel-unchecked]').disabled = form.querySelector('#fuelFullTank').checked;
    });
  });
})();
