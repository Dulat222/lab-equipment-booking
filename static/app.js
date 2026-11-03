
(() => {
  const button = document.getElementById('soundBtn');
  const audio = document.getElementById('notify');

  if (!button) return;

  button.addEventListener('click', async () => {
    try {
      if (audio) {
        audio.currentTime = 0;
        await audio.play();
        return;
      }
    } catch (error) {
      // Если аудиофайлов нет, используем сигнал браузера.
    }

    try {
      const AudioContextClass =
        window.AudioContext || window.webkitAudioContext;

      if (!AudioContextClass) {
        alert('Браузер не поддерживает воспроизведение звука.');
        return;
      }

      const context = new AudioContextClass();
      if (context.state === 'suspended') {
        await context.resume();
      }

      const oscillator = context.createOscillator();
      const volume = context.createGain();

      oscillator.type = 'sine';
      oscillator.frequency.value = 880;

      volume.gain.setValueAtTime(0.001, context.currentTime);
      volume.gain.exponentialRampToValueAtTime(
        0.15, context.currentTime + 0.03
      );
      volume.gain.exponentialRampToValueAtTime(
        0.001, context.currentTime + 0.35
      );

      oscillator.connect(volume);
      volume.connect(context.destination);

      oscillator.start();
      oscillator.stop(context.currentTime + 0.36);
      oscillator.addEventListener('ended', () => context.close());
    } catch (error) {
      alert('Не удалось воспроизвести звук. Проверь громкость браузера.');
    }
  });
})();

// Usability fix after user testing (B2): limit the return-date picker to 14 days after pick-up.
(() => {
  const start = document.getElementById('booking_date');
  const end = document.getElementById('return_date');
  if (!start || !end) return;
  const MAX_DAYS = 14;
  const iso = (d) => d.toISOString().slice(0, 10);
  start.addEventListener('change', () => {
    if (!start.value) return;
    const s = new Date(start.value + 'T00:00:00Z');
    const max = new Date(s.getTime() + (MAX_DAYS - 1) * 86400000);
    end.min = start.value;
    end.max = iso(max);
    if (end.value && (end.value < end.min || end.value > end.max)) end.value = '';
  });
})();
