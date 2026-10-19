import { typesetMath, enhanceImages } from './article-renderer.js';
import './styles.css';
const article = document.querySelector('.narrative') || document.querySelector('article');
if (article) { typesetMath(article); enhanceImages(article); }
