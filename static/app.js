
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
