/**
 * Resume Evaluator AI - Application Logic
 */

const { createApp, ref, computed, onMounted } = Vue;

const app = createApp({
    setup() {
        // State Management
        const fileInput = ref(null);
        const selectedIndex = ref(null);
        const candidates = ref([]);
        const errorMessage = ref('');
        const showError = ref(false);
        const sortOrder = ref('date'); // 'date' | 'score'

        const sortedCandidates = computed(() => {
            const list = [...candidates.value];
            if (sortOrder.value === 'score') {
                return list.sort((a, b) => {
                    const scoreA = a.result ? (a.result.total_score || 0) : -1;
                    const scoreB = b.result ? (b.result.total_score || 0) : -1;
                    // Descending score, processed items first
                    return scoreB - scoreA;
                });
            }
            // Default: Keep original order (which is usually date desc from backend)
            // But if we want to be explicit about date:
            // Since backend sends sorted by ctime desc, index order is date order.
            return list;
        });

        const currentCandidate = computed(() => {
            if (selectedIndex.value === null) return null;
            // selectedIndex tracks the index in the SORTED list now
            return sortedCandidates.value[selectedIndex.value];
        });

        // Load History
        const loadHistory = async () => {
            try {
                const res = await fetch('/api/candidates');
                if (res.ok) {
                    candidates.value = await res.json();
                }
            } catch (error) {
                console.error('Failed to load history:', error);
            }
        };

        onMounted(() => {
            loadHistory();
        });

        // File Upload Methods
        const triggerFileInput = () => fileInput.value.click();

        const handleFileUpload = async (event) => {
            const files = event.target.files;
            if (!files.length) return;

            for (let i = 0; i < files.length; i++) {
                const file = files[i];
                // Add placeholder
                const newCandidate = {
                    filename: file.name,
                    id: null,
                    status: 'pending', // pending -> parsing -> analyzing -> done
                    image_urls: [],
                    result: null,
                    pageCount: 0
                };
                candidates.value.unshift(newCandidate); // Add to top

                // Find index to process (it's 0 since we unshifted, but tracking by obj is safer if async changes happen)
                // Actually, let's just pass the object to processCandidate
                processCandidate(newCandidate, file);
            }
            // Clear input
            event.target.value = '';
        };

        // Candidate Processing
        const processCandidate = async (candidate, fileObj) => {
            // No need to lookup by index, use object ref

            try {
                // 1. Upload & Parse PDF
                candidate.status = 'parsing';
                const formData = new FormData();
                formData.append('file', fileObj);

                const uploadRes = await fetch('/api/upload_pdf', {
                    method: 'POST',
                    body: formData
                });

                if (!uploadRes.ok) {
                    const errorData = await uploadRes.json().catch(() => ({}));
                    const errorMsg = errorData.detail || `Upload failed (${uploadRes.status})`;
                    throw new Error(`Failed to upload ${candidate.filename}: ${errorMsg}`);
                }
                const uploadData = await uploadRes.json();

                candidate.id = uploadData.id;
                candidate.image_urls = uploadData.image_urls;
                candidate.pageCount = uploadData.page_count;
                candidate.status = 'parsed'; // ready for analysis

                // 2. Analyze with VLM
                candidate.status = 'analyzing';

                const analyzePayload = {
                    candidate_id: candidate.id
                };

                const analyzeRes = await fetch('/api/analyze', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(analyzePayload)
                });

                if (!analyzeRes.ok) {
                    const errorData = await analyzeRes.json().catch(() => ({}));
                    const errorMsg = errorData.detail || `Analysis failed (${analyzeRes.status})`;
                    throw new Error(`Failed to analyze ${candidate.filename}: ${errorMsg}`);
                }
                const analyzeData = await analyzeRes.json();

                candidate.result = analyzeData;
                candidate.name = analyzeData.candidate_name;
                candidate.status = 'done';

            } catch (error) {
                console.error('Processing error:', error);
                candidate.status = 'error';
                handleError(`Error processing ${candidate.filename}: ${error.message}`);
            }
        };

        // Clear Cache
        const clearCache = async () => {
            if (!confirm('Are you sure you want to clear all history? This cannot be undone.')) return;
            try {
                const res = await fetch('/api/cache', { method: 'DELETE' });
                if (res.ok) {
                    candidates.value = [];
                    selectedIndex.value = null;
                } else {
                    throw new Error('Failed to clear cache');
                }
            } catch (error) {
                handleError(error.message);
            }
        };

        // Delete Candidate
        const deleteCandidate = async (candidate, event) => {
            if (event) event.stopPropagation();
            if (!confirm(`Delete ${candidate.filename}?`)) return;

            try {
                // If it's a pending/placeholder, just remove from list
                if (!candidate.id) {
                    const idx = candidates.value.indexOf(candidate);
                    if (idx > -1) candidates.value.splice(idx, 1);
                    return;
                }

                const res = await fetch(`/api/candidates/${candidate.id}`, { method: 'DELETE' });
                if (res.ok) {
                    const idx = candidates.value.indexOf(candidate);
                    if (idx > -1) candidates.value.splice(idx, 1);

                    // Reset selection if the deleted one was selected
                    // This is a bit tricky with sorted list logic + selection index. 
                    // Simplest is to just deselect if current view is invalid? 
                    // Or relying on Vue reactivity might just work but 'selectedIndex' is an integer index.
                    // If we delete item 2, item 3 becomes 2. The view might shift.
                    // Let's just set to null to be safe for now, or handle smoothly.
                    // Actually, if we delete the *currently selected* candidate:
                    if (currentCandidate.value === candidate) {
                        selectedIndex.value = null;
                    } else if (currentCandidate.value) {
                        // We need to re-find the new index of the current candidate in the sorted list?
                        // But 'currentCandidate' is computed from selectedIndex.
                        // If we don't update selectedIndex, we point to a different person.
                        // Ideally we track selection by ID, not index.
                    }

                    // To fix the selection jumping issue properly:
                    // Implementing selection by ID would be better but requires more changes.
                    // For now, let's just deselect.
                    selectedIndex.value = null;

                } else {
                    throw new Error("Failed to delete");
                }
            } catch (e) {
                handleError("Delete failed: " + e.message);
            }
        };

        const toggleSort = () => {
            sortOrder.value = sortOrder.value === 'date' ? 'score' : 'date';
            selectedIndex.value = null; // Clear selection to avoid confusion
        };

        // Remove Duplicates
        const removeDuplicates = async () => {
            if (!confirm("Remove duplicate candidates? Keeps the most recent upload.")) return;
            try {
                const res = await fetch(`/api/candidates/deduplicate`, { method: 'DELETE' });
                if (res.ok) {
                    const data = await res.json();
                    handleError(`Removed ${data.deleted_count} duplicates.`, 3000);
                    loadHistory(); // Reload list
                } else {
                    throw new Error("Failed to deduplicate");
                }
            } catch (e) {
                handleError("Deduplication failed: " + e.message);
            }
        };

        // Selection Methods
        const selectCandidate = (index) => {
            selectedIndex.value = index;
        };

        // Utility Methods
        const getScoreColor = (score) => {
            if (score >= 35) return 'bg-green-100 text-green-700';
            if (score >= 25) return 'bg-yellow-100 text-yellow-700';
            return 'bg-red-100 text-red-700';
        };

        const formatKey = (key) => {
            return key.replace(/_/g, ' ');
        };

        // Error handling methods
        const handleError = (message, duration = 5000) => {
            errorMessage.value = message;
            showError.value = true;
            setTimeout(() => {
                showError.value = false;
            }, duration);
        };

        const clearError = () => {
            showError.value = false;
            errorMessage.value = '';
        };


        return {
            fileInput, triggerFileInput, handleFileUpload,
            candidates, selectedIndex, selectCandidate, currentCandidate,
            getScoreColor, formatKey,
            errorMessage, showError, clearError,
            clearCache, deleteCandidate, removeDuplicates,
            sortedCandidates, sortOrder, toggleSort
        };
    }
});

app.config.compilerOptions.isCustomElement = (tag) => tag.includes('ion-icon');
app.mount('#app');
