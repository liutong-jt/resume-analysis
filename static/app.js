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

        const currentCandidate = computed(() => {
            if (selectedIndex.value === null) return null;
            return candidates.value[selectedIndex.value];
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
                const newInd = candidates.value.push({
                    filename: file.name,
                    id: null,
                    status: 'pending', // pending -> parsing -> analyzing -> done
                    image_urls: [],
                    result: null,
                    pageCount: 0
                }) - 1;

                // Start process
                processCandidate(newInd, file);
            }
            // Clear input
            event.target.value = '';
        };

        // Candidate Processing
        const processCandidate = async (index, fileObj) => {
            const candidate = candidates.value[index];

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
            clearCache
        };
    }
});

app.config.compilerOptions.isCustomElement = (tag) => tag.includes('ion-icon');
app.mount('#app');
