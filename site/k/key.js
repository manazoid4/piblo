// Reads p and c from the tap link and asks /api/key whether the card is a genuine Piblo Key.
(async () => {
  const query = new URLSearchParams(location.search);
  const heading = document.getElementById('result');
  const detail = document.getElementById('detail');
  const p = query.get('p');
  const c = query.get('c');
  if (!p || !c) {
    heading.textContent = 'Not recognised';
    detail.textContent = 'Tap your Piblo Key on your phone to open this page with a fresh code.';
    return;
  }
  try {
    const response = await fetch(`/api/key?p=${encodeURIComponent(p)}&c=${encodeURIComponent(c)}`, { cache: 'no-store' });
    const body = await response.json();
    if (body.genuine === true) {
      heading.textContent = 'Genuine Piblo Key';
      detail.textContent = 'This card was made by Piblo.';
    } else {
      heading.textContent = 'Not recognised';
      detail.textContent = 'We could not confirm this card. Try tapping again.';
    }
  } catch {
    heading.textContent = 'Not recognised';
    detail.textContent = 'We could not reach the check. Try again in a moment.';
  }
})();
