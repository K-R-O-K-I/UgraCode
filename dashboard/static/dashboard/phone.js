document.querySelectorAll('[data-phone]').forEach(input => {
  input.addEventListener('input', () => {
    let digits = input.value.replace(/\D/g, '').slice(0, 11);
    if (!digits) { input.value = ''; return; }
    if (digits[0] === '7') digits = '8' + digits.slice(1);
    if (digits[0] !== '8') digits = ('8' + digits).slice(0, 11);
    input.value = digits[0] + (digits.length > 1 ? ' (' + digits.slice(1,4) : '') +
      (digits.length > 4 ? ') ' + digits.slice(4,7) : '') +
      (digits.length > 7 ? ' ' + digits.slice(7) : '');
  });
});
