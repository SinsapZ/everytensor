$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
$documentPath = [IO.Path]::GetFullPath([string]$request.path)
$position = $request.location
$extension = [IO.Path]::GetExtension($documentPath).ToLowerInvariant()
try {
    switch ($extension) {
        '.docx' {
            $application = New-Object -ComObject Word.Application
            $application.AutomationSecurity = 3
            $application.Options.UpdateLinksAtOpen = $false
            $application.Visible = $true
            $document = $application.Documents.Open($documentPath, $false, $true, $false)
            $located = $false
            if ($position.table) {
                $range = $document.Tables.Item([int]$position.table).Rows.Item([int]$position.row).Range
                $located = $true
            }
            if (-not $located -and $position.paragraph) {
                $range = $document.Paragraphs.Item([int]$position.paragraph).Range
                $located = $true
            }
            if (-not $located) { throw 'No supported Word location' }
            # Search only inside the recorded anchor; repeated text elsewhere
            # in the document must not redirect this hit to a different place.
            if ($request.text) {
                $needle = [string]$request.text
                if ($needle.Length -gt 180) { $needle = $needle.Substring(0,180) }
                $match = $range.Duplicate
                $match.Find.ClearFormatting()
                $match.Find.MatchWildcards = $false
                $match.Find.Wrap = 0
                if ($match.Find.Execute($needle)) { $range = $match }
            }
            $range.Select()
        }
        '.pptx' {
            $application = New-Object -ComObject PowerPoint.Application
            $application.AutomationSecurity = 3
            $application.Visible = -1
            $document = $application.Presentations.Open($documentPath, -1, 0, -1)
            $application.ActiveWindow.View.GotoSlide([int]$position.slide)
        }
        '.xlsx' {
            $application = New-Object -ComObject Excel.Application
            $application.AutomationSecurity = 3
            $application.Visible = $true
            $document = $application.Workbooks.Open($documentPath, 0, $true)
            $sheet = $document.Worksheets.Item([string]$position.sheet)
            $sheet.Activate()
            $application.Goto($sheet.Range('A'+[string]$position.row), $true)
        }
        default { throw 'Unsupported Office extension' }
    }
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
