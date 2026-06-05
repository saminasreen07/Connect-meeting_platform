// ── Connect Landing JS ──

// Nav scroll state
const nav = document.getElementById('nav');
if (nav) {
  window.addEventListener('scroll', () => {
    nav.classList.toggle('scrolled', window.scrollY > 8);
  }, { passive: true });
}

// Hamburger
const hamburger = document.getElementById('hamburger');
const navLinks = document.getElementById('navLinks');
if (hamburger && navLinks) {
  hamburger.addEventListener('click', () => {
    navLinks.classList.toggle('open');
  });
  document.addEventListener('click', e => {
    if (!hamburger.contains(e.target) && !navLinks.contains(e.target)) {
      navLinks.classList.remove('open');
    }
  });
}

// Scroll reveal
const revealEls = document.querySelectorAll('.reveal');
const observer = new IntersectionObserver(entries => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      entry.target.classList.add('visible');
      observer.unobserve(entry.target);
    }
  });
}, { threshold: 0.1, rootMargin: '0px 0px -40px 0px' });
revealEls.forEach(el => observer.observe(el));

// FAQ accordion
document.querySelectorAll('.faq-q').forEach(btn => {
  btn.addEventListener('click', () => {
    const item = btn.closest('.faq-item');
    const isActive = item.classList.contains('active');
    document.querySelectorAll('.faq-item.active').forEach(i => i.classList.remove('active'));
    if (!isActive) item.classList.add('active');
    btn.setAttribute('aria-expanded', !isActive);
  });
});

// Join input — focus clears placeholder on mobile
const joinInput = document.querySelector('.join-input');
const joinBtn = document.querySelector('.join-btn');
if (joinInput && joinBtn) {
  joinBtn.addEventListener('click', () => {
    const val = joinInput.value.trim();
    if (!val) { joinInput.focus(); return; }
    // In a real app, redirect to the meeting
    window.location.href = '/accounts/login/?next=/join/' + encodeURIComponent(val);
  });
  joinInput.addEventListener('keydown', e => {
    if (e.key === 'Enter') joinBtn.click();
  });
}

// Stagger reveal delays for feature & testi cards
document.querySelectorAll('.features-grid .feature-card, .testi-grid .testi-card').forEach((el, i) => {
  el.style.transitionDelay = (i % 3) * 80 + 'ms';
});
